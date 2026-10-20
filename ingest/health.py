"""Watches the decoded packet stream for trouble: lost packets, device reboots, a silent link.

The parser already counts damage it can see in the bytes (CRC errors, skipped bytes).
This looks at the packets that made it through and asks what *they* say:
- seq jumped forward      -> packets were lost
- board uptime went back  -> the device rebooted
- nothing for a while     -> the link (or the device) went quiet
"""

from collections import deque

SILENCE_S = 0.5  # no packets for this long counts as "link silent"


class LinkHealth:
    def __init__(self):
        self.packets = 0
        self.dropped = 0
        self.reboots = 0
        self._last_seq = None
        self._last_t_ms = None
        self._last_arrival = None
        self._recent_arrivals = deque()  # host times of packets in the last second

    def update(self, sample, now):
        """Record one decoded packet that arrived at host time `now` (seconds).

        Returns a description if this packet reveals a reboot, otherwise None.
        """
        event = None
        if self._last_t_ms is not None and sample.t_ms < self._last_t_ms:
            # The board's uptime clock went backwards: it restarted.
            self.reboots += 1
            event = f"device reboot (board had been up {self._last_t_ms / 1000:.1f} s)"
        elif self._last_seq is not None:
            expected = (self._last_seq + 1) % 256
            self.dropped += (sample.seq - expected) % 256

        self.packets += 1
        self._last_seq = sample.seq
        self._last_t_ms = sample.t_ms
        self._last_arrival = now
        self._recent_arrivals.append(now)
        return event

    def packets_per_second(self, now):
        while self._recent_arrivals and self._recent_arrivals[0] <= now - 1.0:
            self._recent_arrivals.popleft()
        return len(self._recent_arrivals)

    def is_silent(self, now):
        return self._last_arrival is None or now - self._last_arrival > SILENCE_S

    def snapshot(self, now, parser):
        """Every health number in one place, for the GUI and for publishing."""
        return {
            "packets_per_s": self.packets_per_second(now),
            "dropped": self.dropped,
            "reboots": self.reboots,
            "crc_errors": parser.crc_errors,
            "bytes_skipped": parser.bytes_skipped,
            "silent": self.is_silent(now),
        }
