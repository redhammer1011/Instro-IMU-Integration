"""Explains device reboots using the power-supply readings: brownout, or something else?

Health can tell that the board rebooted, but not why. The supply readings can: if the
voltage dipped below the board's minimum just before the reboot, it was a brownout.
If the supply was healthy, the cause is inside the board (firmware crash, watchdog, ...).
"""

from collections import deque

MIN_OPERATING_V = 4.5  # the board's minimum supply voltage (from its datasheet)
LOOKBACK_S = 1.0  # how far before the reboot to look for a voltage dip


class RebootDiagnoser:
    def __init__(self):
        self._readings = deque()  # (time, volts), oldest first

    def add_voltage(self, now, volts):
        """Record one supply-voltage reading taken at host time `now` (seconds)."""
        self._readings.append((now, volts))
        while self._readings and self._readings[0][0] < now - 10.0:
            self._readings.popleft()  # keep 10 s of history; older readings can't matter

    def explain(self, reboot_time):
        """Describe the most likely cause of a reboot detected at host time `reboot_time`."""
        window = [v for t, v in self._readings if reboot_time - LOOKBACK_S <= t <= reboot_time]
        if not window:
            return "reboot (no supply readings to check the cause)"
        lowest = min(window)
        if lowest < MIN_OPERATING_V:
            return f"brownout reboot: supply dipped to {lowest:.2f} V (board needs {MIN_OPERATING_V} V)"
        return f"reboot with a healthy supply (lowest {lowest:.2f} V): suspect firmware crash or watchdog"
