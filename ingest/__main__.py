"""Record IMU telemetry and supply readings to one file, with live status in the terminal.

    python -m ingest                  (start the bench first: python -m bench.run_bench)
    python -m ingest --imu COM3       (a real board on a real serial port)
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

from instro.lib.publishers import FilePublisher, SharedPublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

from ingest.instrument import SerialTelemetry


def main():
    args = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    args.add_argument("--imu", default="socket://127.0.0.1:7000", help="serial port or URL of the IMU board")
    args.add_argument("--psu", default="TCPIP0::127.0.0.1::5025::SOCKET", help="VISA address of the bench supply")
    args.add_argument("--out", type=Path, default=Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args.add_argument("--seconds", type=float, help="stop after this many seconds (default: run until Ctrl+C)")
    options = args.parse_args()

    # One file for everything. SharedPublisher lets both instruments write to it safely.
    recording = SharedPublisher(FilePublisher(options.out, format="jsonl", custom_file_name="telemetry"))
    psu = InstroPSU(name="psu", driver=SimulatedPSU(options.psu), num_channels=1, publishers=[recording.clone()])
    imu = SerialTelemetry("imu", options.imu, psu=psu, publishers=[recording.clone()])

    psu.open()
    imu.open()
    imu.start()
    print(f"Recording to {options.out / 'telemetry.jsonl'}  (Ctrl+C to stop)\n")

    events_shown = 0
    stop_at = time.monotonic() + options.seconds if options.seconds else None
    try:
        while stop_at is None or time.monotonic() < stop_at:
            time.sleep(1)
            for when, description in imu.events[events_shown:]:
                print(f"  ** {datetime.fromtimestamp(when):%H:%M:%S}  {description}")
            events_shown = len(imu.events)
            print(status_line(imu))
    except KeyboardInterrupt:
        pass
    finally:
        imu.close()
        psu.close()
        recording.close()
        print(f"\nSaved {options.out / 'telemetry.jsonl'}")


def status_line(imu):
    h = imu.health.snapshot(time.monotonic(), imu.parser)
    link = "SILENT" if h["silent"] else f"{h['packets_per_s']:3d} pkt/s"
    supply = f"{imu.supply_volts:.2f} V" if imu.supply_volts is not None else "  --  "
    accel = "  --  " if imu.latest is None else "accel z {:.3f} g".format(imu.latest.accel_mg[2] / 1000)
    return (
        f"{link} | supply {supply} | {accel} | dropped {h['dropped']} | crc errors {h['crc_errors']}"
        f" | skipped {h['bytes_skipped']} B | reboots {h['reboots']}"
    )


if __name__ == "__main__":
    main()
