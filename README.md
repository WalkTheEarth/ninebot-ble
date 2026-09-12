# Ninebot Scooter BLE Python client

Python client for interfacing with a Ninebot (Segway) eKickScooter over Bluetooth Low Energy (BLE).

It primarily uses the BLE UART characteristic for communication with the scooter. Both the newer
encrypted protocol (using the [miauth](https://github.com/dnandha/miauth) library) and the older
plain (unencrypted) protocol are supported, with automatic detection during the handshake — so
both recent firmware and old legacy firmware work.

The project's primary objective is to support the
[Ninebot Scooter Home Assistant integration](https://github.com/WalkTheEarth/ninebot-integration)
but will work for more use-cases as well.

- Read-only client: it only reads registers, it never writes to the scooter
- Automatic protocol detection (encrypted / legacy plain)
- Session pairing with the scooter's power-button confirm, with a 60-second wait and clear log message
- Fault-tolerant register reads: registers a model does not implement are skipped, not fatal
- Model detection from the serial number, with graceful fallback for unknown models

## Requirements

- Python 3.9+
- A Bluetooth adapter reachable by [bleak](https://github.com/hbldh/bleak)
  (BlueZ on Linux, built-in stack on macOS and Windows)

## Installation

From PyPI:

```
pip install ninebot-ble
```

Or straight from GitHub for the latest changes:

```
pip install git+https://github.com/WalkTheEarth/ninebot-ble
```

## Usage

A command-line client for testing purposes is shipped. Running it with no arguments scans for a
scooter, connects, pairs if needed, and dumps all available registers:

```
ninebot-ble
```

To read only specific registers, pass flags named after the registers (see `--help` for the full
list):

```
ninebot-ble --total-mileage --remaining-battery-capacity --scooter-serial-number
```

### Pairing

On the first connect to a scooter (or after it has forgotten the session), the scooter asks for a
confirmation: the log prints `Please press power button on scooter!` — short-press the scooter's
power button once within 60 seconds. This is the same confirmation ScooterHacking Utility performs.
Pairing is per-session only; the scooter does not permanently bind to this client and nothing is
written to it.

If the scooter is asleep it may not advertise: short-press its power button to wake it and retry.
The scan window is 30 seconds.

### Python API

High-level sensor interface (used by the Home Assistant integration):

```python
import asyncio

from home_assistant_bluetooth import BluetoothServiceInfo

from ninebot_ble import NinebotBleSensor, async_scooter_scan


async def main() -> None:
    device, adv = await async_scooter_scan()
    nb = NinebotBleSensor()
    nb.update(BluetoothServiceInfo.from_advertisement(device, adv, "test"))
    update = await nb.async_poll(device)
    for key, value in update.entity_values.items():
        print(update.entity_descriptions[key].native_unit_of_measurement, value.native_value)
    await nb.disconnect()


asyncio.run(main())
```

Low-level client for individual registers:

```python
import asyncio

from ninebot_ble import CtrlIdx, NinebotClient, async_scooter_scan


async def main() -> None:
    device, _ = await async_scooter_scan()
    client = NinebotClient()
    await client.connect(device)
    try:
        serial = await client.read_reg(CtrlIdx.NB_INF_SN)
        print("Serial:", serial)
    finally:
        await client.disconnect()


asyncio.run(main())
```

The register table (`CtrlIdx`, `BmsIdx`) in
[`ninebot_ble/register.py`](ninebot_ble/register.py) is the source of truth for what can be read.

## Supported models

Model detection is based on the scooter serial number (see the
[ScooterHacking.org wiki](https://wiki.scooterhacking.org/) for the serial number anatomy) and
covers the Ninebot E-series (E22/E25/E45), older ES-series (ES1–ES4, SNSC1.x), the Max family
(G30/G30D/G30E/G30LP/G30LE/G30LD, SNSC2.x rentals, Seat Mó, Audi EKS), the F-series
(F20–F60) and the ES3 Plus. The x3 generation is covered at family level: `1TE` serials
resolve to the E3 / E3 Pro family and `1CG` to the Max G3 family (variant letters within
these families are not publicly documented yet). Unknown serials are handled gracefully and
simply shown as `<series>-series`.

Discovery accepts scooters advertising the classic Ninebot manufacturer id `0x424E` (16974), the
newer `0x434E` (17230) with a serial-style broadcast name, and the serial-style name alone.

Individual registers that a particular model or firmware does not implement are skipped instead
of failing the whole update, so partial data from unusual models is still reported.

Protocol details are documented in [`docs/protocol.md`](docs/protocol.md).

## Troubleshooting

- **No scooter found** — wake the scooter with a short press of its power button (it stops
  advertising when idle), keep it within a few meters, and make sure no other app (ScooterHacking
  Utility, the Segway app) is holding the BLE connection.
- **Scan finds nothing at all** — check that the Bluetooth adapter is up (`bluetoothctl power on`,
  or `rfkill unblock bluetooth`) and that your user may use it.
- **Waits for a button press** — that is the pairing confirmation; press the scooter's power
  button once within 60 seconds.
- **Some values missing** — the scooter's firmware does not implement those registers; they are
  skipped on purpose. Check the debug log for which ones.

For anything else, enable debug logging and open an issue with the output:

```python
import logging

logging.basicConfig(level=logging.DEBUG)
```

## Development

Run the offline test suite (no scooter or Bluetooth adapter needed — the handshake is tested
against a fake scooter implementing both protocol generations):

```
pip install -e .[dev]
python -m pytest tests/
```

## Credits

Based on [ownbee/ninebot-ble](https://github.com/ownbee/ninebot-ble), with protocol knowledge
from the [miauth](https://github.com/dnandha/miauth) project and the
[ScooterHacking.org wiki](https://wiki.scooterhacking.org/). Fixes and model support in this fork
were verified against real hardware.

## License

[MIT](LICENSE)
