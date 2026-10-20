"""Turns a raw UART byte stream back into IMU packets.

Bytes arrive in arbitrary chunks: half a packet, three and a half packets, garbage, anything.
feed() keeps leftover bytes between calls and returns every complete, valid packet so far.
"""

from ingest.packet import CRC, HEADER, IMU_PAYLOAD, SYNC, ImuSample, crc16

PACKET_LEN = len(SYNC) + HEADER.size + IMU_PAYLOAD.size + CRC.size


class PacketParser:
    def __init__(self):
        self.crc_errors = 0  # packets thrown away because the CRC didn't match
        self.bytes_skipped = 0  # bytes discarded while hunting for a valid packet
        self._buffer = bytearray()  # bytes received but not parsed yet

    def feed(self, data: bytes) -> list[ImuSample]:
        """Add newly received bytes; return every complete, valid packet now available."""
        self._buffer += data
        samples = []

        while True:
            # 1. Find the start of a packet. Everything before it is junk.
            start = self._buffer.find(SYNC)
            if start == -1:
                # No sync anywhere. Keep a trailing 0xAA: it may be the first half of a sync
                # whose 0x55 arrives in the next read.
                keep = 1 if self._buffer.endswith(SYNC[:1]) else 0
                self._skip(len(self._buffer) - keep)
                return samples
            self._skip(start)

            # 2. Wait until the length byte has arrived, then sanity-check it.
            if len(self._buffer) < len(SYNC) + 1:
                return samples
            if self._buffer[2] != IMU_PAYLOAD.size:
                self._skip(1)  # not a real packet start; search again from the next byte
                continue

            # 3. Wait until the whole packet has arrived.
            if len(self._buffer) < PACKET_LEN:
                return samples

            # 4. Check the CRC (it covers len..end of payload, not the sync bytes).
            body = bytes(self._buffer[2 : PACKET_LEN - CRC.size])
            (received_crc,) = CRC.unpack(self._buffer[PACKET_LEN - CRC.size : PACKET_LEN])
            if received_crc != crc16(body):
                self.crc_errors += 1
                self._skip(1)  # skip only 1 byte: a real packet may start inside this one
                continue

            # 5. Valid packet: decode it and remove it from the buffer.
            samples.append(decode(body))
            del self._buffer[:PACKET_LEN]

    def _skip(self, count):
        del self._buffer[:count]
        self.bytes_skipped += count


def decode(body: bytes) -> ImuSample:
    """body = header + payload, already CRC-checked."""
    _length, _msg_id, seq = HEADER.unpack(body[: HEADER.size])
    t_ms, ax, ay, az, gx, gy, gz, temp = IMU_PAYLOAD.unpack(body[HEADER.size :])
    return ImuSample(seq=seq, t_ms=t_ms, accel_mg=(ax, ay, az), gyro_dps10=(gx, gy, gz), temp_c100=temp)
