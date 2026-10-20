"""Explains what the IMU stream can't explain by itself, using the power-supply readings.

The packets alone show THAT something happened: the board rebooted, or the link went silent.
The supply's voltage and current show WHY:
- reboot after the voltage dipped below the board's minimum -> brownout
- reboot with a healthy supply                              -> something inside the board
- silent link, board still drawing its normal current      -> the board is fine: check the cable
- silent link, board drawing almost nothing                -> the board has no power or is in reset
"""

from collections import deque

MIN_OPERATING_V = 4.5  # the board's minimum supply voltage (from its datasheet)
RUNNING_MIN_A = 0.05  # a running board draws ~100 mA
OFF_MAX_A = 0.02  # a board that's off or held in reset draws almost nothing
REBOOT_LOOKBACK_S = 1.0  # how far before a reboot to look for a voltage dip
SILENCE_LOOKBACK_S = 0.3  # how far back to average the current when the link goes silent


class FaultDiagnoser:
    def __init__(self):
        self._readings = deque()  # (time, volts, amps), oldest first

    def add_reading(self, now, volts, amps):
        """Record one supply reading taken at host time `now` (seconds)."""
        self._readings.append((now, volts, amps))
        while self._readings and self._readings[0][0] < now - 10.0:
            self._readings.popleft()  # keep 10 s of history; older readings can't matter

    def _window(self, start, end):
        return [(v, a) for t, v, a in self._readings if start <= t <= end]

    def explain_reboot(self, reboot_time):
        """Most likely cause of a reboot detected at host time `reboot_time`."""
        window = self._window(reboot_time - REBOOT_LOOKBACK_S, reboot_time)
        if not window:
            return "reboot (no supply readings to check the cause)"
        lowest = min(v for v, _ in window)
        if lowest < MIN_OPERATING_V:
            return f"brownout reboot: supply dipped to {lowest:.2f} V (board needs {MIN_OPERATING_V} V)"
        return f"reboot with a healthy supply (lowest {lowest:.2f} V): suspect firmware crash or watchdog"

    def explain_silence(self, now):
        """Most likely cause of the link going silent, judged from the board's current draw."""
        window = self._window(now - SILENCE_LOOKBACK_S, now)
        if not window:
            return "link went silent (no supply readings to check why)"
        amps = sum(a for _, a in window) / len(window)
        lowest = min(v for v, _ in window)
        if amps >= RUNNING_MIN_A:
            return f"link went silent, but the board is drawing {amps * 1000:.0f} mA (running): check the serial cable"
        if amps <= OFF_MAX_A and lowest < MIN_OPERATING_V:
            return f"link went silent: board lost power (supply {lowest:.2f} V, drawing {amps * 1000:.0f} mA)"
        if amps <= OFF_MAX_A:
            return f"link went silent: board drawing only {amps * 1000:.0f} mA with good supply: off or held in reset"
        return f"link went silent; board drawing {amps * 1000:.0f} mA (neither running nor off: unusual)"
