"""Parser for Ninebot ESC (controller) serial numbers.

Serial number anatomy (14 characters, e.g. ``N4GS D 19 39 C 0123``):

- ``N4G``  product identifier (model series)
- ``D``    product version (SN prefix, model specific)
- ``D``    manufacturing line at the factory
- ``19``   year of production
- ``39``   week of production
- ``C``    product revision
- ``0123`` unit serial number for that week

Model tables follow the ScooterHacking.org wiki (nbeseries, nbesx, nbmax,
nbfseries) so that as many Ninebot scooter models as possible are recognized.
Unknown prefixes degrade gracefully instead of raising.
"""

from __future__ import annotations

import datetime

from sensor_state_data.enum import StrEnum


class SerialParser:
    class ProductSeries(StrEnum):
        E_ES = "N2G"  # E-series (E22/E25/E45) and older ES-series (ES1-ES4)
        MAX = "N4G"  # Max G30 family
        MAX_RENTAL_20 = "N4L"  # SNSC2.0 rental
        MAX_PLUS_RENTAL = "N4Z"  # SNSC2.3 rental
        MAX_PRO_RENTAL = "NAG"  # SNSC2.2A rental
        MAX_PLUS_US = "A2S"  # SNSC2.3 US rental
        SEAT_MO = "N4Y"  # Seat Mó (G30D)
        AUDI_EKS = "NTG"  # Audi EKS (G30D)
        ES3_PLUS = "N6G"  # ES3 Plus (Costco)
        F = "N5G"  # F-series

    SERIES_NAMES = {
        ProductSeries.E_ES: "E/ES",
        ProductSeries.MAX: "Max",
        ProductSeries.MAX_RENTAL_20: "Max SNSC2.0",
        ProductSeries.MAX_PLUS_RENTAL: "Max Plus SNSC2.3",
        ProductSeries.MAX_PRO_RENTAL: "Max Pro SNSC2.2A",
        ProductSeries.MAX_PLUS_US: "Max Plus SNSC2.3 US",
        ProductSeries.SEAT_MO: "Seat Mó G30D",
        ProductSeries.AUDI_EKS: "Audi EKS G30D",
        ProductSeries.ES3_PLUS: "ES3 Plus",
        ProductSeries.F: "F",
    }

    PRODUCT_VERSION_MAPPING = {
        ProductSeries.E_ES: {
            # Legacy E-series (E22/E25/E45)
            "D": "E22",
            "G": "E22E",
            "I": "E22D",
            "V": "E25",
            "Y": "E25D",
            "X": "E25E",
            "Z": "E25A",
            "R": "E45D",
            "O": "E45E",
            "M": "E45E",
            "Q": "E45 (30 km/h)",
            # Older ES-series
            "S": "ES4 (Black)",
            "H": "ES4 (Black)",
            "A": "ES4 (Silver)",
            "P": "ES4 (Silver)",
            "J": "ES4 (Silver)",
            "B": "ES4 (Silver, no lights)",
            "W": "ES2 (Black)",
            "K": "ES2 (Black)",
            "N": "ES2 (Black)",
            "T": "ES2 (Silver)",
            "U": "ES2L (Black)",
            "C": "ES3 (Black)",
            "F": "ES1 (Black)",
            "E": "ES1 (Black)",
        },
        ProductSeries.MAX: {
            "S": "G30P (30 km/h)",
            "C": "G30 (25 km/h)",
            "E": "G30D blue (20 km/h)",
            "P": "G30E (25 km/h)",
            "N": "G30LP (30 km/h)",
            "A": "G30LE (25 km/h)",
            "O": "G30LE (25 km/h)",
            "M": "G30LD (20 km/h)",
            "T": "G30M Maserati (25 km/h)",
            "2": "SNSC2.2A (25 km/h)",
            "0": "SNSC2.3 (25 km/h)",
            "1": "Audi EKS G30D (20 km/h)",
        },
        ProductSeries.SEAT_MO: {
            "C": "Seat Mó (G30D, 20 km/h)",
        },
        ProductSeries.AUDI_EKS: {
            "1": "Audi EKS (G30D, 20 km/h)",
        },
        ProductSeries.ES3_PLUS: {
            "A": "ES3 Plus (Costco)",
        },
        ProductSeries.F: {
            "A": "F20",
            "B": "F20D",
            "C": "F30",
            "D": "F30D",
            "E": "F40",
            "F": "F40E",
            "G": "F40D",
            "H": "F60",
            "I": "F60D/F60E",
            "J": "F60D/F60E",
            "M": "F60A/F60 Asia",
            "N": "F25",
            "O": "F20A",
            "Q": "F30E",
            "R": "F40A",
            "S": "F20E/F20D (?)",
            "V": "F40",
            "W": "F25E",
        },
    }

    def __init__(self, serial: str) -> None:
        serial = serial.strip()
        if len(serial) < 9 or not serial[5:9].isdigit():
            raise ValueError(f"Unsupported serial number {serial!r}")
        try:
            self.product_series = self.ProductSeries(serial[:3])
        except ValueError:
            # Unknown series; keep the raw identifier so the device still shows up.
            self.product_series = None  # type: ignore[assignment]
        self._raw_series = serial[:3]
        self._product_version = None
        if self.product_series is not None:
            self._product_version = self.PRODUCT_VERSION_MAPPING.get(self.product_series, {}).get(serial[3])
        self._production_line = serial[4]
        self._year = 2000 + int(serial[5:7])
        self._week = int(serial[7:9])
        self.product_revision = serial[9] if len(serial) > 9 else None
        self.weekly_serial = int(serial[10:14]) if len(serial) >= 14 and serial[10:14].isdigit() else None

    @property
    def production_date(self) -> datetime.datetime:
        return datetime.datetime.fromisocalendar(self._year, min(self._week, 53), 1)

    @property
    def product_version(self) -> str:
        if self._product_version is not None:
            return self._product_version
        if self.product_series is not None:
            return f"{self.SERIES_NAMES.get(self.product_series, str(self.product_series))}-series"
        return f"{self._raw_series}-series"

    def __str__(self) -> str:
        return f"Ninebot {self.product_version}"
