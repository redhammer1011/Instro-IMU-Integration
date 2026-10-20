# Instro IMU Integration

Stream binary UART telemetry from a board into [Instro](https://github.com/nominal-io/instro), alongside your bench instruments.

Bench monitor showing a brownout and an unplugged serial cable:

https://github.com/user-attachments/assets/019edbfd-5a00-41bb-a602-da59782a7b95


Instro gives you one Python API for test equipment like power supplies, DMMs, and DAQs. This project covers the other side of the bench: the device under test sending its own telemetry over a serial port. It decodes the stream, keeps track of link health (CRC errors, dropped packets, reboots), and records everything through Instro's publishers on the same timeline as the bench power supply. Because it can see both, it can also tell you why the data stopped: a brownout, a firmware reset, or just an unplugged cable.

It ships with a simulated bench (a fake IMU board and Instro's simulated power supply), so you can try it without any hardware.

## Quickstart

```bash
git clone https://github.com/redhammer1011/Instro-IMU-Integration.git
cd Instro-IMU-Integration
uv sync
uv run python -m ingest.gui --simulate
```

This opens the monitor with a simulated bench running behind it. Use the buttons on the right to sag the supply voltage or break the serial link, and watch the event log explain what happened.

## Installation

You'll need Python 3.12 or newer. The easiest way to install everything is with [uv](https://docs.astral.sh/uv/getting-started/installation/):

```bash
uv sync
```

If you'd rather use pip, create a virtual environment and install the dependencies directly, then run the commands below from the repo root without the `uv run` prefix:

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install instro pyserial pyside6-essentials pyqtgraph numpy
```

## Usage

### Monitor with the simulated bench

```bash
uv run python -m ingest.gui --simulate
```

The window shows live plots of the IMU data and the supply readings, link health, and an event log. Every run is recorded to `runs/<date-time>/telemetry.jsonl`.

With `--simulate`, a fault-injection panel lets you break the simulated hardware. The monitor doesn't know which control you used; it has to work it out from the data.

| Try | You should see |
|---|---|
| **Drop to 4.3 V**, then **Restore 5.0 V** | *board lost power*, then *brownout reboot* |
| **Serial cable unplugged** | *link went silent, but the board is drawing 100 mA (running): check the serial cable* |
| **Bad cable** | status turns amber and the CRC and dropped-packet counts climb, but no bad values reach the plots |
| **Firmware crash** | *reboot with a healthy supply: suspect firmware crash or watchdog* |
| **Marginal 4.6 V** | no event, but noisy sensor data: the board runs, but its readings aren't trustworthy |

### Running the bench separately

To mirror a real setup, run the simulated bench and the monitor as two programs. In one terminal:

```bash
uv run python -m bench.run_bench
```

The bench takes keyboard commands: `b` bad cable, `u` unplug or replug the cable, `r` firmware crash, `v 4.3` set the supply voltage, `s` status, `q` quit.

In another terminal, start the monitor, either the GUI or a terminal-only recorder that prints a status line every second:

```bash
uv run python -m ingest.gui
uv run python -m ingest
uv run python -m ingest --seconds 30
```

### Options

| Option | Default | Description |
|---|---|---|
| `--imu` | `socket://127.0.0.1:7000` | Serial port of the board (`COM3`, `/dev/ttyUSB0`) or a pyserial URL |
| `--psu` | `TCPIP0::127.0.0.1::5025::SOCKET` | VISA address of the bench supply |
| `--out` | `runs/<date-time>` | Folder to save the recording in |
| `--simulate` | off | GUI only: also run the simulated bench, with fault-injection controls |
| `--seconds` | until Ctrl+C | Terminal recorder only: stop after this many seconds |

### Using it in your own code

`SerialTelemetry` is a regular Instro instrument, so it works with any Instro publisher:

```python
import time

from instro.lib.publishers import FilePublisher
from ingest.instrument import SerialTelemetry

imu = SerialTelemetry(
    name="imu",
    port_url="socket://127.0.0.1:7000",  # or "COM3", "/dev/ttyUSB0"
    publishers=[FilePublisher(directory="data/", format="jsonl")],
)
imu.open()
imu.start()  # reads, decodes, and publishes in the background

print(imu.latest)  # most recent decoded packet
print(imu.health.snapshot(time.monotonic(), imu.parser))

imu.close()
```

Pass an opened `InstroPSU` as `psu=` and it will also read the supply and explain reboots and silences. To send data to Nominal instead of a file, use Instro's `NominalCorePublisher`.

## Real hardware

**Board:** pass its serial port with `--imu COM3` (115200 baud). The board needs to send the packet format below.

**Power supply:** replace `SimulatedPSU(...)` in `ingest/session.py` with the Instro driver for your supply (for example `RigolDP800` or `KeysightE36100`), and pass its VISA address with `--psu`. The rest of the code stays the same.

## Packet format

25 bytes, little-endian, sent at 100 Hz:

```
| AA 55 | len | msg_id | seq | t_ms | ax ay az | gx gy gz | temp | CRC-16 |
   2      1      1       1     4       3 x 2      3 x 2      2       2
```

| Field | Type | Description |
|---|---|---|
| `AA 55` | bytes | Sync marker |
| `len` | u8 | Payload length (18) |
| `msg_id` | u8 | Message type (`0x01` = IMU sample) |
| `seq` | u8 | Packet counter, wraps 255 to 0 |
| `t_ms` | u32 | Milliseconds since the board booted |
| `ax ay az` | i16 | Acceleration, milli-g |
| `gx gy gz` | i16 | Rotation rate, 0.1 deg/s |
| `temp` | i16 | Temperature, 0.01 °C |
| CRC-16 | u16 | CRC-16/CCITT-FALSE over `len` through the end of the payload |

The format is defined in `ingest/packet.py`.

## Recorded data

Each run writes one JSON Lines file, written by Instro's `FilePublisher`, with nanosecond timestamps:

| Channel | Description |
|---|---|
| `imu.accel_{x,y,z}_g` | Acceleration in g |
| `imu.gyro_{x,y,z}_dps` | Rotation rate in deg/s |
| `imu.temp_c` | Board temperature in °C |
| `imu.seq`, `imu.board_uptime_s` | Raw packet counter and board uptime |
| `imu.link.*` | Link health: `packets_per_s`, `dropped`, `crc_errors`, `bytes_skipped`, `reboots`, `silent` |
| `imu.event` | Text annotations: diagnosed reboots and silences, link lost or restored |
| `psu.ch1.voltage`, `psu.ch1.current` | Supply readings, published by Instro's `InstroPSU` |

## How faults are diagnosed

The IMU stream shows *that* something happened. The supply readings show *why*.

| IMU stream | Supply | Diagnosis |
|---|---|---|
| Board rebooted | Voltage dipped below 4.5 V | Brownout |
| Board rebooted | Voltage stayed normal | Firmware crash or watchdog reset |
| Data stopped | Board still drawing ~100 mA | Board is running; check the serial cable |
| Data stopped | Board drawing ~4 mA, voltage low | Board lost power |

Reboots are detected from the board's uptime counter going backwards, since the 8-bit sequence counter wraps every 2.56 seconds anyway. The parser resyncs one byte at a time after a bad CRC, so a corrupted packet can't hide the start of the next one.

## Development

Run the tests:

```bash
uv run pytest
```

The 26 tests cover the parser (split packets, garbage, bit flips, dropped bytes, false sync bytes, random noise), link health, and fault diagnosis. They don't need any hardware or the simulated bench.

```
bench/
  fake_imu.py       simulated IMU board
  run_bench.py      simulated bench: Instro's simulated supply powering the board
ingest/
  packet.py         packet format
  parser.py         byte stream to packets, with resync
  health.py         dropped packets, reboots, packet rate, silence
  events.py         fault diagnosis from supply readings
  instrument.py     SerialTelemetry, the Instro instrument
  session.py        connects the supply and the board and starts recording
  gui.py            GUI
  __main__.py       terminal recorder
tests/
```

## Known limitations

- The supply is polled every 50 ms, so a shorter voltage dip can reset the board without being seen.
- Only one message type is supported, and the format lives in code rather than a config file.
- If the connection to the supply drops, it isn't re-established until you restart.
