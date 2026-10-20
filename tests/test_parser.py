"""Tests for the packet parser. Each test is one way a real serial link misbehaves."""

import random

from ingest.packet import SYNC, ImuSample, encode
from ingest.parser import PacketParser


def sample(seq, ax=10):
    return ImuSample(seq=seq, t_ms=1000 + seq * 10, accel_mg=(ax, -5, 1000), gyro_dps10=(3, 0, -2), temp_c100=2550)


def packets(*seqs):
    return b"".join(encode(sample(s)) for s in seqs)


# --- The happy path -------------------------------------------------------------


def test_one_clean_packet_decodes_to_the_same_values():
    parser = PacketParser()
    assert parser.feed(encode(sample(7))) == [sample(7)]


def test_several_packets_in_one_read():
    parser = PacketParser()
    assert parser.feed(packets(1, 2, 3)) == [sample(1), sample(2), sample(3)]


# --- Bytes arrive in awkward chunks ----------------------------------------------


def test_packet_split_across_two_reads():
    data = encode(sample(1))
    parser = PacketParser()
    assert parser.feed(data[:10]) == []  # not complete yet: keep the bytes, return nothing
    assert parser.feed(data[10:]) == [sample(1)]


def test_one_byte_at_a_time():
    parser = PacketParser()
    received = []
    for byte in packets(1, 2):
        received += parser.feed(bytes([byte]))
    assert received == [sample(1), sample(2)]


def test_joining_in_the_middle_of_a_packet():
    # The board was already streaming when we connected, so the first bytes are a packet's back half.
    data = packets(1, 2)[12:]
    parser = PacketParser()
    assert parser.feed(data) == [sample(2)]


# --- A noisy or damaged link ------------------------------------------------------


def test_garbage_before_a_packet_is_skipped_and_counted():
    garbage = bytes([0x01, 0x02, 0x03, 0xFF, 0x00])
    parser = PacketParser()
    assert parser.feed(garbage + encode(sample(1))) == [sample(1)]
    assert parser.bytes_skipped == len(garbage)


def test_flipped_bit_fails_crc_and_the_next_packet_still_decodes():
    bad = bytearray(encode(sample(1)))
    bad[10] ^= 0x01  # one flipped bit inside the payload
    parser = PacketParser()
    assert parser.feed(bytes(bad) + encode(sample(2))) == [sample(2)]
    assert parser.crc_errors == 1


def test_dropped_byte_loses_that_packet_but_not_the_ones_after():
    damaged = bytearray(encode(sample(1)))
    del damaged[8]  # a byte lost in the middle of the packet
    parser = PacketParser()
    assert parser.feed(bytes(damaged) + packets(2, 3)) == [sample(2), sample(3)]


def test_impossible_length_is_rejected_without_waiting_for_more_bytes():
    fake_header = SYNC + bytes([200])  # nothing in this protocol is 200 bytes long
    parser = PacketParser()
    assert parser.feed(fake_header + encode(sample(1))) == [sample(1)]


def test_fake_sync_inside_a_payload_does_not_swallow_the_next_packet():
    # ax = 0x55AA is sent little-endian as the bytes AA 55: a "sync" that isn't one.
    tricky = encode(sample(1, ax=0x55AA))
    assert SYNC in tricky[2:]
    # Join mid-packet, before the fake sync, so the parser hits the fake one first.
    data = tricky[5:] + encode(sample(2))
    parser = PacketParser()
    assert parser.feed(data) == [sample(2)]


# --- It must survive anything ------------------------------------------------------


def test_random_junk_never_crashes_the_parser():
    rng = random.Random(1)
    parser = PacketParser()
    for _ in range(2000):
        parser.feed(rng.randbytes(rng.randint(0, 64)))


def test_every_packet_survives_random_junk_between_them():
    rng = random.Random(2)
    stream = b"".join(rng.randbytes(rng.randint(0, 30)) + encode(sample(s)) for s in range(50))
    parser = PacketParser()
    received = []
    position = 0
    while position < len(stream):  # deliver it in random-sized chunks, like a real serial port
        chunk = rng.randint(1, 40)
        received += parser.feed(stream[position : position + chunk])
        position += chunk
    assert [p.seq for p in received] == list(range(50))
