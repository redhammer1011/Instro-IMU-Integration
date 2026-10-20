"""Tests for link-health tracking. Times are passed in, so nothing here depends on the real clock."""

from ingest.health import LinkHealth
from ingest.packet import ImuSample


def sample(seq, t_ms):
    return ImuSample(seq=seq, t_ms=t_ms, accel_mg=(0, 0, 1000), gyro_dps10=(0, 0, 0), temp_c100=2500)


def feed(health, seqs, t0_ms=0, start=0.0):
    """Feed packets with the given seq numbers, 10 ms apart on both clocks."""
    events = []
    for i, seq in enumerate(seqs):
        event = health.update(sample(seq, t0_ms + 10 * i), now=start + 0.01 * i)
        if event:
            events.append(event)
    return events


def test_clean_stream_has_no_drops():
    health = LinkHealth()
    feed(health, range(100))
    assert health.packets == 100
    assert health.dropped == 0


def test_counter_wrapping_255_to_0_is_not_a_drop():
    health = LinkHealth()
    feed(health, [253, 254, 255, 0, 1])
    assert health.dropped == 0


def test_seq_jump_counts_the_missing_packets():
    health = LinkHealth()
    feed(health, [40, 41, 44])  # 42 and 43 never arrived
    assert health.dropped == 2


def test_drops_across_the_wrap():
    health = LinkHealth()
    feed(health, [253, 254, 2])  # 255, 0, 1 are missing
    assert health.dropped == 3


def test_board_clock_going_backwards_is_a_reboot_not_a_drop():
    health = LinkHealth()
    feed(health, [10, 11, 12], t0_ms=41_000)
    events = feed(health, [0, 1, 2], t0_ms=0, start=1.0)
    assert health.reboots == 1
    assert health.dropped == 0
    assert events == ["device reboot (board had been up 41.0 s)"]


def test_packets_per_second_counts_only_the_last_second():
    health = LinkHealth()
    feed(health, [i % 256 for i in range(300)])  # 3 s of packets at 100 Hz
    assert health.packets_per_second(now=2.995) == 100


def test_silence_is_detected():
    health = LinkHealth()
    assert health.is_silent(now=0.0)  # nothing received yet
    feed(health, range(10))
    assert not health.is_silent(now=0.2)
    assert health.is_silent(now=1.0)
