"""The simulated test bench: Instro's simulated power supply feeding the fake IMU board.

    python -m bench.run_bench

What's running:
- Instro's SCPI power-supply simulator on 127.0.0.1:5025. The ingester controls and reads it
  with Instro's normal SimulatedPSU driver, exactly like a real bench supply.
- The fake IMU on socket://127.0.0.1:7000, powered by that supply. It reads the voltage
  at its terminals directly from the simulator (the supply's output is the board's input).

Type commands here to play "the world" (things that happen to the hardware):
    b       toggle bad cable (garbage bytes, dropped bytes, bit flips)
    u       unplug / replug the serial cable (the board keeps running)
    r       force a reboot (like a watchdog reset; the supply stays healthy)
    v 4.3   set the supply voltage by hand (normally the ingester's GUI does this)
    s       print bench status
    q       quit
"""

from instro.psu.scpi_sim_server import SimulatedPSU as PSUSimulator
from instro.psu.scpi_sim_server import SimulatedPSUServer

from bench.fake_imu import FakeImu

PSU_PORT = 5025
NOMINAL_V = 5.0
LEAD_RESISTANCE_OHMS = 0.05  # short, thick bench leads (the simulator defaults to 10 ohm)


def start_power_supply():
    server = SimulatedPSUServer(PSUSimulator(num_channels=1), host="127.0.0.1", port=PSU_PORT)
    server.start()
    with server.lock:
        channel = server.psu.channels[0]
        channel.load.resistance = NOMINAL_V / 0.1  # until the board reports its draw: ~100 mA
        channel.load.probe_resistance = LEAD_RESISTANCE_OHMS
        # Power on the way a bench operator would: 5 V, 1 A limit, output on.
        for command in (":SOUR1:VOLT 5.000", ":SOUR1:CURR 1.000", ":OUTP1:STAT ON"):
            server.psu.process_scpi_command(command)
    return server


def set_board_current(server, amps):
    """The simulator models a load as a resistor, so turn the board's current draw into ohms."""
    with server.lock:
        server.psu.channels[0].load.resistance = NOMINAL_V / amps
        server.psu._update()  # recompute volts and amps now (Instro's own UI does the same)


def board_voltage(server):
    """Voltage at the board's power pins: the supply output after the lead resistance."""
    with server.lock:
        return server.psu.channels[0].load_voltage


def status(server, imu):
    with server.lock:
        channel = server.psu.channels[0]
        volts, amps, setpoint = channel.load_voltage, channel.current, channel.voltage_setpoint
    board = f"ON, {imu.packets_since_boot} packets since boot" if imu.powered else "OFF (browned out)"
    cable = "UNPLUGGED" if imu.cable_unplugged else "BAD" if imu.bad_cable else "ok"
    return f"supply set {setpoint:.2f} V | board sees {volts:.3f} V, {amps * 1000:.0f} mA | board {board} | cable {cable}"


class SimulatedBench:
    """The whole simulated bench: Instro's supply powering the fake IMU. Used here and by the GUI."""

    def __init__(self):
        self.server = start_power_supply()
        self.imu = FakeImu(
            get_supply_voltage=lambda: board_voltage(self.server),
            set_current_draw=lambda amps: set_board_current(self.server, amps),
        )
        self.imu.start()

    def set_voltage(self, volts):
        """Turn the supply's knob directly (the ingester normally does this through Instro instead)."""
        with self.server.lock:
            self.server.psu.process_scpi_command(f":SOUR1:VOLT {volts:.3f}")

    def status(self):
        return status(self.server, self.imu)

    def stop(self):
        self.imu.stop()
        self.server.shutdown()


def main():
    bench = SimulatedBench()
    imu = bench.imu
    print(__doc__)
    print(f"Power supply (SCPI): TCPIP0::127.0.0.1::{PSU_PORT}::SOCKET")
    print("IMU serial stream:   socket://127.0.0.1:7000")
    print(bench.status())

    try:
        while True:
            command = input("> ").strip().lower()
            if command == "q":
                break
            elif command == "b":
                imu.bad_cable = not imu.bad_cable
                print(f"bad cable {'ON' if imu.bad_cable else 'OFF'}")
            elif command == "u":
                imu.cable_unplugged = not imu.cable_unplugged
                print(f"serial cable {'UNPLUGGED' if imu.cable_unplugged else 'plugged in'}")
            elif command == "r":
                imu.reboot()
                print("board rebooted")
            elif command.startswith("v "):
                bench.set_voltage(float(command.split()[1]))
                print(bench.status())
            elif command == "s":
                print(bench.status())
            elif command:
                print("commands: b, u, r, v <volts>, s, q")
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        bench.stop()


if __name__ == "__main__":
    main()
