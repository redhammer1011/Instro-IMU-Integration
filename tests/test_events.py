"""Tests for fault diagnosis. Supply readings come every 50 ms, as the ingester polls them."""

from ingest.events import FaultDiagnoser


def record(diagnoser, start, end, volts, amps=0.100):
    t = start
    while t < end:
        diagnoser.add_reading(t, volts, amps)
        t += 0.05


# --- Reboots --------------------------------------------------------------------


def test_voltage_dip_before_reboot_is_a_brownout():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 5.0, 5.0)
    record(diagnoser, 5.0, 5.5, 4.3, amps=0.005)  # supply sags, board dies
    record(diagnoser, 5.5, 6.0, 5.0)  # supply back, board reboots
    assert diagnoser.explain_reboot(reboot_time=5.52).startswith("brownout reboot: supply dipped to 4.30 V")


def test_reboot_with_steady_supply_points_at_the_firmware():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 6.0, 4.99)
    assert diagnoser.explain_reboot(reboot_time=5.5) == (
        "reboot with a healthy supply (lowest 4.99 V): suspect firmware crash or watchdog"
    )


def test_an_old_dip_does_not_explain_a_later_reboot():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 1.0, 4.3)  # a dip long before
    record(diagnoser, 1.0, 6.0, 5.0)
    assert "healthy supply" in diagnoser.explain_reboot(reboot_time=5.5)


def test_no_readings_says_so():
    assert FaultDiagnoser().explain_reboot(reboot_time=1.0) == "reboot (no supply readings to check the cause)"


# --- Silence ----------------------------------------------------------------------


def test_silent_while_drawing_normal_current_blames_the_cable():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 3.0, 5.0, amps=0.100)
    assert "check the serial cable" in diagnoser.explain_silence(now=3.0)


def test_silent_with_low_voltage_and_no_current_is_lost_power():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 2.0, 5.0, amps=0.100)
    record(diagnoser, 2.0, 3.0, 4.3, amps=0.004)
    assert diagnoser.explain_silence(now=3.0) == "link went silent: board lost power (supply 4.30 V, drawing 4 mA)"


def test_silent_with_good_voltage_but_no_current_is_off_or_in_reset():
    diagnoser = FaultDiagnoser()
    record(diagnoser, 0.0, 3.0, 5.0, amps=0.003)
    assert "off or held in reset" in diagnoser.explain_silence(now=3.0)
