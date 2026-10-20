"""SerialTelemetry: the IMU's UART stream as an Instro instrument.

Follows Instro's custom-instrument pattern (examples/custom/simple_temp_controller.py):
subclass Instrument, do the work in a background daemon function, and publish Measurements.
Whatever publishers are attached (a file, Nominal Core, ...) receive every decoded packet,
the link-health numbers, and reboot diagnoses.
"""

import time

import serial
from instro.lib import Instrument, Measurement

from ingest.events import RebootDiagnoser
from ingest.health import LinkHealth
from ingest.parser import PacketParser

LOOP_INTERVAL_S = 0.02  # read the port 50 times a second (about 2 packets each time)
SUPPLY_POLL_S = 0.05  # read the supply voltage 20 times a second
HEALTH_PUBLISH_S = 0.25  # publish link health 4 times a second
RECONNECT_S = 1.0  # after losing the serial port, try to reopen it this often
PACKET_SPACING_NS = 10_000_000  # the board sends every 10 ms


class SerialTelemetry(Instrument):
    def __init__(self, name, port_url, psu=None, baudrate=115200, **kwargs):
        """
        port_url: "socket://127.0.0.1:7000" for the simulated bench, or "COM3" for a real board.
        psu:      an opened InstroPSU powering the board, used to explain reboots (optional).
        """
        super().__init__(name, **kwargs)
        self._port_url = port_url
        self._baudrate = baudrate
        self._psu = psu
        self._port = None

        self.parser = PacketParser()
        self.health = LinkHealth()
        self.diagnoser = RebootDiagnoser()
        self.latest = None  # most recent decoded sample
        self.supply_volts = None  # most recent supply reading
        self.events = []  # (wall-clock time, description): reboots, lost/restored connections

        self._next_supply_poll = 0.0
        self._next_health_publish = 0.0
        self._next_reconnect = 0.0
        self.background_interval = LOOP_INTERVAL_S
        self.add_background_daemon_function(self._poll)

    def open(self):
        self._port = self._open_port()  # fail loudly at startup if the port can't be opened

    def close(self):
        super().close()  # stops the background loop and closes the publishers
        if self._port is not None:
            self._port.close()

    def _open_port(self):
        return serial.serial_for_url(self._port_url, baudrate=self._baudrate, timeout=0)

    def _poll(self):
        """One pass of the background loop: supply, serial port, health."""
        now = time.monotonic()

        if self._psu is not None and now >= self._next_supply_poll:
            self._read_supply(now)

        samples = self.parser.feed(self._read_port(now))
        if samples:
            for sample in samples:
                reboot = self.health.update(sample, now)
                if reboot:
                    self._record_event(self.diagnoser.explain(now))
            self.latest = samples[-1]
            self._publish_samples(samples)

        if now >= self._next_health_publish:
            self._publish_health(now)
            self._next_health_publish = now + HEALTH_PUBLISH_S

    def _read_supply(self, now):
        self._next_supply_poll = now + SUPPLY_POLL_S
        try:
            self.supply_volts = self._psu.get_voltage(1).latest  # InstroPSU publishes this itself
        except Exception as error:  # the supply went away (unplugged, powered off, crashed)
            self._psu = None  # stop polling it; reboots can no longer be explained
            self.supply_volts = None
            self._record_event(f"lost connection to the power supply ({type(error).__name__})")
            return
        self.diagnoser.add_voltage(now, self.supply_volts)

    def _read_port(self, now):
        """Bytes waiting on the serial port. Survives the cable being unplugged and plugged back in."""
        if self._port is None:
            if now < self._next_reconnect:
                return b""
            try:
                self._port = self._open_port()
            except serial.SerialException:
                self._next_reconnect = now + RECONNECT_S
                return b""
            self._record_event("serial link restored")
        try:
            return self._port.read(4096)
        except serial.SerialException:
            self._port.close()
            self._port = None
            self._next_reconnect = now + RECONNECT_S
            self._record_event("serial link lost: retrying every second")
            return b""

    def _publish_samples(self, samples):
        # Packets that arrive together were sent 10 ms apart, so back-date the earlier ones.
        arrived = time.time_ns()
        timestamps = [arrived - (len(samples) - 1 - i) * PACKET_SPACING_NS for i in range(len(samples))]
        prefix = self.name
        self.publish(
            Measurement(
                channel_data={
                    f"{prefix}.accel_x_g": [s.accel_mg[0] / 1000 for s in samples],
                    f"{prefix}.accel_y_g": [s.accel_mg[1] / 1000 for s in samples],
                    f"{prefix}.accel_z_g": [s.accel_mg[2] / 1000 for s in samples],
                    f"{prefix}.gyro_x_dps": [s.gyro_dps10[0] / 10 for s in samples],
                    f"{prefix}.gyro_y_dps": [s.gyro_dps10[1] / 10 for s in samples],
                    f"{prefix}.gyro_z_dps": [s.gyro_dps10[2] / 10 for s in samples],
                    f"{prefix}.temp_c": [s.temp_c100 / 100 for s in samples],
                    f"{prefix}.seq": [float(s.seq) for s in samples],
                    f"{prefix}.board_uptime_s": [s.t_ms / 1000 for s in samples],
                },
                timestamps=timestamps,
                tags=self.default_tags,
            )
        )

    def _publish_health(self, now):
        snapshot = self.health.snapshot(now, self.parser)
        self.publish(
            Measurement(
                channel_data={f"{self.name}.link.{key}": [float(value)] for key, value in snapshot.items()},
                timestamps=[time.time_ns()],
                tags=self.default_tags,
            )
        )

    def _record_event(self, description):
        self.events.append((time.time(), description))
        self.publish(self._package_measurement("event", description, time.time_ns()))
