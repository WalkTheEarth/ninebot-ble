# Ninebot Scooter BLE Python client

Python client for interfacing with a Ninebot scooter using bluetooth low energy (BLE).

It is primarely using the BLE UART characteristic for communication with the scooter. Both the
newer encrypted protocol (using the [miauth](https://github.com/dnandha/miauth) library) and the
older plain (unencrypted) protocol are supported, with automatic detection during the handshake —
so both recent firmware and old legacy firmware work.

The projects primary objective is to support Home Assistant integration but will work for more
use-cases as well.

## Usage

Installation:

```
pip install ninebot-ble
```

A command-line client for testing purposes are shipped:

```
ninebot-ble --help
```

## Supported models

Model detection is based on the scooter serial number (see the
[ScooterHacking.org wiki](https://wiki.scooterhacking.org/) for the serial number anatomy) and
covers the Ninebot E-series (E22/E25/E45), older ES-series (ES1–ES4, SNSC1.x), the Max family
(G30/G30D/G30E/G30LP/G30LE/G30LD, SNSC2.x rentals, Seat Mó, Audi EKS), the F-series
(F20–F60) and the ES3 Plus. The x3 generation is covered at family level: `1TE` serials
resolve to the E3 / E3 Pro family and `1CG` to the Max G3 family (variant letters within
these families are not publicly documented yet). Unknown serials are handled gracefully and
simply shown as `<series>-series`.

Individual registers that a particular model or firmware does not implement are skipped instead
of failing the whole update, so partial data from unusual models is still reported.

## Troubleshoot

Support for many models is community tested — if your scooter misbehaves, enable debug logging
(`logging.basicConfig(level=logging.DEBUG)`) and open an issue with the output.
