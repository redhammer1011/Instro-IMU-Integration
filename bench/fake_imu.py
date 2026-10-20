"""Fake IMU board that streams telemetry packets, standing in for real hardware.

It serves bytes over TCP. pyserial opens "socket://127.0.0.1:7000" exactly like it opens
a COM port, so the ingester's code is the same as it would be for a real board.

The board is powered by a (simulated) bench supply:
- below BROWNOUT_V it stops running, and when power returns it boots from scratch (seq resets to 0)
- below MARGINAL_V it still runs, but its sensor readings get noisy
"""

import math
import random
import socket
import threading
import time

from ingest.packet import ImuSample, encode

PORT = 7000
RATE_HZ = 100
BROWNOUT_V = 4.5
MARGINAL_V = 4.75


class FakeImu:
    def __init__(self, get_supply_voltage, port=PORT):
        self.get_supply_voltage = get_supply_voltage
        self.port = port
        self.bad_cable = False
        self._seq = 0
        self._boot_time = time.monotonic()
        self._powered = True
        self._stop = threading.Event()
        self._reader = None  # the connected ingester, if any ("is the cable plugged in?")
        self._reader_lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        threading.Thread(target=self._accept_readers, daemon=True).start()

    def stop(self):
        self._stop.set()

    def reboot(self):
        self._seq = 0
        self._boot_time = time.monotonic()

    @property
    def powered(self):
        return self._powered

    @property
    def packets_since_boot(self):
        return self._seq

    def _accept_readers(self):
        """Plug a reader into the 'cable'. One at a time, like a real UART; the newest one wins."""
        server = socket.create_server(("127.0.0.1", self.port))
        server.settimeout(0.5)
        while not self._stop.is_set():
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            with self._reader_lock:
                if self._reader is not None:
                    self._reader.close()
                self._reader = conn
        server.close()

    def _run(self):
        """The board's main loop. Runs whether or not anyone is listening, like real firmware."""
        next_tick = time.monotonic()
        while not self._stop.is_set():
            packet = self._next_packet()
            if packet is not None:
                self._transmit(corrupt(packet) if self.bad_cable else packet)
            next_tick += 1 / RATE_HZ
            time.sleep(max(0.0, next_tick - time.monotonic()))
        with self._reader_lock:
            if self._reader is not None:
                self._reader.close()

    def _transmit(self, packet):
        with self._reader_lock:
            if self._reader is None:
                return  # nobody is listening: the bytes just go nowhere
            try:
                self._reader.sendall(packet)
            except OSError:
                self._reader.close()  # the reader went away
                self._reader = None

    def _next_packet(self):
        volts = self.get_supply_voltage()
        if volts < BROWNOUT_V:
            self._powered = False
            return None
        if not self._powered:
            self._powered = True
            self.reboot()
        sample = self._read_sensors(noisy=volts < MARGINAL_V)
        self._seq += 1
        return encode(sample)

    def _read_sensors(self, noisy):
        t = time.monotonic() - self._boot_time

        # The board rocks gently: tilt angle = 3 deg * sin(2*pi*0.5 Hz*t)
        angle = math.radians(3.0 * math.sin(math.pi * t))
        rate_dps = 3.0 * math.pi * math.cos(math.pi * t)  # derivative of the tilt angle, deg/s

        noise = 300 if noisy else 5
        accel = (1000 * math.sin(angle), 0.0, 1000 * math.cos(angle))  # gravity, tilted, in mg
        gyro = (rate_dps * 10, 0.0, 0.0)  # in 0.1 deg/s
        temp_c = 25.0 + 5.0 * (1 - math.exp(-t / 60))  # warms up after power-on

        return ImuSample(
            seq=self._seq,
            t_ms=int(t * 1000),
            accel_mg=tuple(to_int16(a + random.gauss(0, noise)) for a in accel),
            gyro_dps10=tuple(to_int16(g + random.gauss(0, noise)) for g in gyro),
            temp_c100=to_int16(temp_c * 100),
        )


def to_int16(value):
    return max(-32768, min(32767, round(value)))


def corrupt(packet):
    """Simulate a bad cable: sometimes add garbage, drop a byte, or flip a bit."""
    data = bytearray(packet)
    roll = random.random()
    if roll < 0.05:
        data[0:0] = random.randbytes(random.randint(1, 10))  # noise before the packet
    elif roll < 0.10:
        del data[random.randrange(len(data))]  # a lost byte
    elif roll < 0.15:
        data[random.randrange(len(data))] ^= 1 << random.randrange(8)  # one flipped bit
    return bytes(data)


if __name__ == "__main__":
    # Run on its own with a steady 5 V supply: python -m bench.fake_imu
    imu = FakeImu(get_supply_voltage=lambda: 5.0)
    imu.start()
    print(f"Fake IMU streaming on socket://127.0.0.1:{PORT} (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        imu.stop()
