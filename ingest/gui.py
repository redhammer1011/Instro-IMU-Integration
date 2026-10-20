"""Live bench monitor: IMU plots, supply readings, link health, and diagnosed events in one window.

    python -m ingest.gui --simulate   (runs the simulated bench too, with fault-injection controls)
    python -m ingest.gui              (connects to a bench that's already running, simulated or real)
"""

import argparse
import sys
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from instro.lib import Measurement
from PySide6 import QtCore, QtWidgets

from ingest.session import Session, add_connection_options

WINDOW_S = 10  # seconds of history shown in the plots
REFRESH_MS = 50  # redraw 20 times a second
GAP_S = 0.25  # no data for longer than this is drawn as a break in the line, not bridged

SURFACE, INK, INK_2 = "#fcfcfb", "#0b0b0b", "#52514e"
AXIS_COLORS = {"x": "#2a78d6", "y": "#eb6834", "z": "#1baf7a"}
GOOD, WARNING, CRITICAL = "#0ca30c", "#fab219", "#d03b3b"


class PlotBuffer:
    """An Instro publisher that keeps the last few seconds of every numeric channel for the plots.

    Instro hands every Measurement to each attached publisher, so the GUI gets its data the same
    way the recording file does.
    """

    def __init__(self, seconds=WINDOW_S):
        self._seconds = seconds
        self._lock = threading.Lock()  # instruments publish from their own threads
        self._data = {}  # channel -> deque of (timestamp_ns, value)

    def publish(self, data, **kwargs):
        if not isinstance(data, Measurement):
            return  # commands (like "set voltage") aren't plotted
        cutoff = data.timestamps[-1] - self._seconds * 1_000_000_000
        with self._lock:
            for channel, values in data.channel_data.items():
                if not values or isinstance(values[0], str):
                    continue  # text channels (events) are shown in the event log instead
                series = self._data.setdefault(channel, deque())
                series.extend(zip(data.timestamps, values))
                while series and series[0][0] < cutoff:
                    series.popleft()

    def close(self):
        pass

    def series(self, channel, now_ns):
        """(seconds before now, values) for one channel, ready to plot."""
        with self._lock:
            points = list(self._data.get(channel, ()))
        if not points:
            return np.empty(0), np.empty(0)
        times, values = np.array(points, dtype=float).T
        seconds = (times - now_ns) / 1e9
        # Insert a NaN wherever data stopped for a while, so the plot shows a gap instead of
        # drawing a straight line across a period when nothing arrived.
        gaps = np.flatnonzero(np.diff(seconds) > GAP_S) + 1
        return np.insert(seconds, gaps, np.nan), np.insert(values, gaps, np.nan)


class Monitor(QtWidgets.QMainWindow):
    def __init__(self, session, buffer, bench=None):
        """bench: a SimulatedBench running in this program, to show fault-injection controls for."""
        super().__init__()
        self.session = session
        self.buffer = buffer
        self.bench = bench
        self._events_shown = 0
        self._error_history = deque()  # (time, total errors) to spot errors in the last second

        self.setWindowTitle("IMU Bench Monitor")
        self.resize(1400, 850)
        root = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(root)
        layout.addWidget(self._build_plots(), stretch=3)
        layout.addWidget(self._build_side_panel(), stretch=1)
        self.setCentralWidget(root)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)

    # ---- layout --------------------------------------------------------------------

    def _build_plots(self):
        pg.setConfigOptions(antialias=True, background=SURFACE, foreground=INK_2)
        plots = pg.GraphicsLayoutWidget()
        self._curves = []  # (channel, curve, scale)

        def add_plot(row, title, units):
            plot = plots.addPlot(row=row, col=0, title=title)
            plot.setLabel("left", units)
            plot.showGrid(x=True, y=True, alpha=0.15)
            plot.setXRange(-WINDOW_S, 0, padding=0)
            plot.setMouseEnabled(x=False, y=False)
            return plot

        accel = add_plot(0, "Acceleration (IMU over UART)", "g")
        gyro = add_plot(1, "Rotation rate (IMU over UART)", "deg/s")
        volts = add_plot(2, "Supply voltage (bench supply via Instro)", "V")
        amps = add_plot(3, "Supply current (bench supply via Instro)", "mA")
        amps.setLabel("bottom", "seconds ago")

        for plot, quantity in ((accel, "accel"), (gyro, "gyro")):
            plot.addLegend(offset=(10, 5), colCount=3)
            units = "g" if quantity == "accel" else "dps"
            for axis, color in AXIS_COLORS.items():
                curve = plot.plot(pen=pg.mkPen(color, width=2), name=axis)
                self._curves.append((f"imu.{quantity}_{axis}_{units}", curve, 1.0))

        volts.setYRange(4.0, 5.3)
        volts.addItem(pg.InfiniteLine(4.5, angle=0, pen=pg.mkPen(CRITICAL, width=1, style=QtCore.Qt.DashLine),
                                      label="board minimum 4.5 V", labelOpts={"color": INK_2, "position": 0.08}))  # fmt: skip
        self._curves.append(("psu.ch1.voltage", volts.plot(pen=pg.mkPen(AXIS_COLORS["x"], width=2)), 1.0))

        amps.setYRange(0, 250)
        self._curves.append(("psu.ch1.current", amps.plot(pen=pg.mkPen(AXIS_COLORS["x"], width=2)), 1000.0))

        for plot in (gyro, volts, amps):
            plot.setXLink(accel)
        return plots

    def _build_side_panel(self):
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        status_row = QtWidgets.QHBoxLayout()
        self._dot = QtWidgets.QLabel()
        self._dot.setFixedSize(16, 16)
        self._status = QtWidgets.QLabel("Waiting for data")
        self._status.setStyleSheet("font-size: 18px; font-weight: bold;")
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status)
        status_row.addStretch()
        layout.addLayout(status_row)

        grid = QtWidgets.QFormLayout()
        self._fields = {}
        for key, label in (
            ("rate", "Packets / s"),
            ("supply", "Supply"),
            ("dropped", "Dropped packets"),
            ("crc", "CRC errors"),
            ("skipped", "Bytes skipped"),
            ("reboots", "Reboots"),
        ):
            self._fields[key] = QtWidgets.QLabel("--")
            grid.addRow(label, self._fields[key])
        layout.addLayout(grid)

        layout.addWidget(QtWidgets.QLabel("<b>Bench supply</b>"))
        buttons = QtWidgets.QHBoxLayout()
        for text, volts in (("Drop to 4.3 V", 4.3), ("Marginal 4.6 V", 4.6), ("Restore 5.0 V", 5.0)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(lambda checked=False, v=volts: self.set_supply(v))
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if self.bench is not None:
            layout.addWidget(self._build_fault_panel())

        layout.addWidget(QtWidgets.QLabel("<b>Events</b> (newest first)"))
        self._events = QtWidgets.QListWidget()
        self._events.setWordWrap(True)
        layout.addWidget(self._events, stretch=1)

        recording = QtWidgets.QLabel(f"Recording to {self.session.out_file}")
        recording.setWordWrap(True)
        recording.setStyleSheet(f"color: {INK_2};")
        layout.addWidget(recording)
        return panel

    def _build_fault_panel(self):
        """Controls that make the simulated hardware misbehave. The monitor never reads these: it
        has to work out what happened from the data, the same as it would on a real bench."""
        box = QtWidgets.QGroupBox("Simulated bench: fault injection")
        layout = QtWidgets.QVBoxLayout(box)
        imu = self.bench.imu

        bad_cable = QtWidgets.QCheckBox("Bad cable (garbage, lost bytes, bit flips)")
        bad_cable.toggled.connect(lambda on: setattr(imu, "bad_cable", on))
        unplugged = QtWidgets.QCheckBox("Serial cable unplugged (board keeps running)")
        unplugged.toggled.connect(lambda on: setattr(imu, "cable_unplugged", on))
        crash = QtWidgets.QPushButton("Firmware crash (reboot with healthy power)")
        crash.clicked.connect(imu.reboot)

        for widget in (bad_cable, unplugged, crash):
            layout.addWidget(widget)
        return box

    # ---- live updates --------------------------------------------------------------------

    def refresh(self):
        now_ns = time.time_ns()
        for channel, curve, scale in self._curves:
            t, values = self.buffer.series(channel, now_ns)
            curve.setData(t, values * scale, connect="finite")  # "finite": don't draw through NaN gaps

        imu = self.session.imu
        health = imu.health.snapshot(time.monotonic(), imu.parser)
        self._fields["rate"].setText(str(health["packets_per_s"]))
        self._fields["dropped"].setText(str(health["dropped"]))
        self._fields["crc"].setText(str(health["crc_errors"]))
        self._fields["skipped"].setText(str(health["bytes_skipped"]))
        self._fields["reboots"].setText(str(health["reboots"]))
        if imu.supply_volts is None:
            self._fields["supply"].setText("not connected")
        else:
            self._fields["supply"].setText(f"{imu.supply_volts:.2f} V   {imu.supply_amps * 1000:.0f} mA")
        self._show_link_status(health)

        for when, description in imu.events[self._events_shown :]:
            self._events.insertItem(0, f"{datetime.fromtimestamp(when):%H:%M:%S}  {description}")
        self._events_shown = len(imu.events)

    def _show_link_status(self, health):
        now = time.monotonic()
        errors = health["crc_errors"] + health["dropped"] + health["bytes_skipped"]
        self._error_history.append((now, errors))
        while self._error_history[0][0] < now - 1.0:
            self._error_history.popleft()
        new_errors = errors > self._error_history[0][1]

        if health["silent"]:
            color, text = CRITICAL, "No data"
        elif new_errors or health["packets_per_s"] < 90:
            color, text = WARNING, "Link degraded"
        else:
            color, text = GOOD, "Receiving"
        self._dot.setStyleSheet(f"background-color: {color}; border-radius: 8px;")
        self._status.setText(text)

    def set_supply(self, volts):
        """Operator action: change the bench supply through Instro, like turning the knob."""
        try:
            self.session.psu.set_voltage(volts, 1)
        except Exception as error:
            self._events.insertItem(0, f"{datetime.now():%H:%M:%S}  could not set the supply ({type(error).__name__})")

    def closeEvent(self, event):
        self._timer.stop()
        self.session.close()
        if self.bench is not None:
            self.bench.stop()
        super().closeEvent(event)


def main():
    args = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_options(args)
    args.add_argument("--simulate", action="store_true", help="also run the simulated bench in this window")
    options = args.parse_args()

    bench = None
    if options.simulate:
        from bench.run_bench import SimulatedBench

        bench = SimulatedBench()  # must be listening before the monitor connects to it

    app = QtWidgets.QApplication(sys.argv[:1])
    buffer = PlotBuffer()
    session = Session(options.imu, options.psu, options.out, extra_publishers=[buffer])
    session.start()
    window = Monitor(session, buffer, bench)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
