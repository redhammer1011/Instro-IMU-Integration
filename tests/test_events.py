"""Tests for reboot diagnosis. Supply readings come every 50 ms, as the ingester will poll them."""

from ingest.events import RebootDiagnoser


def record(diagnoser, start, end, volts):
    t = start
    while t < end:
        diagnoser.add_voltage(t, volts)
        t += 0.05


def test_voltage_dip_before_reboot_is_a_brownout():
    diagnoser = RebootDiagnoser()
    record(diagnoser, 0.0, 5.0, 5.0)
    record(diagnoser, 5.0, 5.5, 4.3)  # supply sags, board dies
    record(diagnoser, 5.5, 6.0, 5.0)  # supply back, board reboots
    assert diagnoser.explain(reboot_time=5.52).startswith("brownout reboot: supply dipped to 4.30 V")


def test_reboot_with_steady_supply_points_at_the_firmware():
    diagnoser = RebootDiagnoser()
    record(diagnoser, 0.0, 6.0, 4.99)
    assert diagnoser.explain(reboot_time=5.5) == (
        "reboot with a healthy supply (lowest 4.99 V): suspect firmware crash or watchdog"
    )


def test_an_old_dip_does_not_explain_a_later_reboot():
    diagnoser = RebootDiagnoser()
    record(diagnoser, 0.0, 1.0, 4.3)  # a dip long before
    record(diagnoser, 1.0, 6.0, 5.0)
    assert "healthy supply" in diagnoser.explain(reboot_time=5.5)


def test_no_readings_says_so():
    assert RebootDiagnoser().explain(reboot_time=1.0) == "reboot (no supply readings to check the cause)"
