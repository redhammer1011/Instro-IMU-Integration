"""Connects to the bench and starts recording. Shared by the terminal program and the GUI."""

from datetime import datetime
from pathlib import Path

from instro.lib.publishers import FilePublisher, SharedPublisher
from instro.psu import InstroPSU
from instro.psu.drivers import SimulatedPSU

from ingest.instrument import SerialTelemetry


def add_connection_options(parser):
    parser.add_argument("--imu", default="socket://127.0.0.1:7000", help="serial port or URL of the IMU board")
    parser.add_argument("--psu", default="TCPIP0::127.0.0.1::5025::SOCKET", help="VISA address of the bench supply")
    parser.add_argument("--out", type=Path, default=Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S"))


class Session:
    """The bench supply and the IMU, both recording into one file."""

    def __init__(self, imu_url, psu_resource, out_dir, extra_publishers=()):
        """extra_publishers: more places every reading should go (the GUI uses this for its plots)."""
        self.out_file = Path(out_dir) / "telemetry.jsonl"
        # One file for everything. SharedPublisher lets both instruments write to it safely.
        self._recording = SharedPublisher(FilePublisher(out_dir, format="jsonl", custom_file_name="telemetry"))
        self.psu = InstroPSU(
            name="psu",
            driver=SimulatedPSU(psu_resource),
            num_channels=1,
            publishers=[self._recording.clone(), *extra_publishers],
        )
        self.imu = SerialTelemetry("imu", imu_url, psu=self.psu, publishers=[self._recording.clone(), *extra_publishers])

    def start(self):
        self.psu.open()
        self.imu.open()
        self.imu.start()

    def close(self):
        self.imu.close()  # stop reading first, so nothing uses the supply while it closes
        self.psu.close()
        self._recording.close()  # the last handle: flushes and closes the file
