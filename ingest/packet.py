"""IMU telemetry packet format, shared by the fake device and the ingester.

Wire layout (little-endian):

    | AA 55 | len | msg_id | seq | payload (len bytes) | CRC-16 |
      sync    1      1       1          18                  2

Payload: t_ms (u32), accel x/y/z (i16, milli-g), gyro x/y/z (i16, 0.1 deg/s), temp (i16, 0.01 C).
The CRC is CRC-16/CCITT-FALSE over len..end of payload (not the sync bytes).
"""

import binascii
import struct
from dataclasses import dataclass

SYNC = b"\xaa\x55"
MSG_IMU = 0x01

HEADER = struct.Struct("<BBB")  # len, msg_id, seq
IMU_PAYLOAD = struct.Struct("<I6hh")  # t_ms, ax, ay, az, gx, gy, gz, temp
CRC = struct.Struct("<H")

PACKET_SIZE = len(SYNC) + HEADER.size + IMU_PAYLOAD.size + CRC.size  # 25 bytes


@dataclass
class ImuSample:
    seq: int
    t_ms: int
    accel_mg: tuple[int, int, int]
    gyro_dps10: tuple[int, int, int]
    temp_c100: int


def crc16(data: bytes) -> int:
    return binascii.crc_hqx(data, 0xFFFF)


def encode(sample: ImuSample) -> bytes:
    payload = IMU_PAYLOAD.pack(
        sample.t_ms & 0xFFFFFFFF,
        *sample.accel_mg,
        *sample.gyro_dps10,
        sample.temp_c100,
    )
    body = HEADER.pack(len(payload), MSG_IMU, sample.seq & 0xFF) + payload
    return SYNC + body + CRC.pack(crc16(body))
