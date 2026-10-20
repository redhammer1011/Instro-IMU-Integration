"""Record IMU telemetry and supply readings to one file, with live status in the terminal.

    python -m ingest                  (start the bench first: python -m bench.run_bench)
    python -m ingest --imu COM3       (a real board on a real serial port)
    python -m ingest.gui              (the same thing, in a window)
"""

import argparse
import time
from datetime import datetime

from ingest.session import Session, add_connection_options


def main():
    args = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_options(args)
    args.add_argument("--seconds", type=float, help="stop after this many seconds (default: run until Ctrl+C)")
    options = args.parse_args()

    session = Session(options.imu, options.psu, options.out)
    session.start()
    print(f"Recording to {session.out_file}  (Ctrl+C to stop)\n")

    events_shown = 0
    stop_at = time.monotonic() + options.seconds if options.seconds else None
    try:
        while stop_at is None or time.monotonic() < stop_at:
            time.sleep(1)
            for when, description in session.imu.events[events_shown:]:
                print(f"  ** {datetime.fromtimestamp(when):%H:%M:%S}  {description}")
            events_shown = len(session.imu.events)
            print(status_line(session.imu))
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
        print(f"\nSaved {session.out_file}")


def status_line(imu):
    h = imu.health.snapshot(time.monotonic(), imu.parser)
    link = "SILENT" if h["silent"] else f"{h['packets_per_s']:3d} pkt/s"
    supply = "    --     " if imu.supply_volts is None else f"{imu.supply_volts:.2f} V {imu.supply_amps * 1000:3.0f} mA"
    accel = "  --  " if imu.latest is None else "accel z {:.3f} g".format(imu.latest.accel_mg[2] / 1000)
    return (
        f"{link} | supply {supply} | {accel} | dropped {h['dropped']} | crc errors {h['crc_errors']}"
        f" | skipped {h['bytes_skipped']} B | reboots {h['reboots']}"
    )


if __name__ == "__main__":
    main()
