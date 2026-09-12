import asyncio
import logging
import time

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .const import NINEBOT_MANUFACTURER_IDS

_LOGGER = logging.getLogger(__name__)


def is_scooter_advertisement(name: str | None, manufacturer_data: dict[int, bytes]) -> bool:
    """Heuristic for recognizing a Ninebot scooter advertisement.

    Older generations advertise with manufacturer id 0x424E, newer ones with
    0x434E and a serial-style local name (e.g. ``1TEFE2517C0419``). Some
    models skip manufacturer data entirely and only expose the Nordic UART
    service, so also match the serial-style name pattern.
    """
    if any(mid in manufacturer_data for mid in NINEBOT_MANUFACTURER_IDS):
        return True
    if name and len(name) in (14, 15) and name[0].isdigit() and name[1].isalpha() and name[1].isupper():
        # Serial-style broadcast name, e.g. ``1TEFE2517C0419``.
        return True
    return False


async def async_scooter_scan() -> tuple[BLEDevice, AdvertisementData]:
    """Scans the Bluetooth network for a ninebot scooter."""
    scan_queue: asyncio.Queue[tuple[BLEDevice, AdvertisementData]] = asyncio.Queue(100)

    async def _on_scan_found(dev: BLEDevice, adv: AdvertisementData) -> None:
        _LOGGER.debug("scan found: %s | %s", dev, adv)
        await scan_queue.put((dev, adv))

    async def scan() -> tuple[BLEDevice, AdvertisementData] | None:
        async with BleakScanner(scanning_mode="active", detection_callback=_on_scan_found):
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    dev, adv = scan_queue.get_nowait()
                    if is_scooter_advertisement(dev.name or adv.local_name, adv.manufacturer_data):
                        return dev, adv
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.1)
        return None

    result = await scan()
    if result is None:
        raise RuntimeError("Unable to find scooter")
    return result
