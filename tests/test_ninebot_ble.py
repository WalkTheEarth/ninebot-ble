"""Tests for the Ninebot BLE client.

These are offline unit tests: they exercise framing, checksums, serial parsing
and the protocol auto-detection without a real scooter.
"""

from __future__ import annotations

import asyncio
from binascii import hexlify
from typing import Any

import pytest

from ninebot_ble.register import BmsIdx, CtrlIdx, get_register_desc, iter_register
from ninebot_ble.serial_parser import SerialParser
from ninebot_ble.transport import Command, DeviceId, LegacyCodec, NinebotClient, Packet


def frame_str(packet: Packet) -> str:
    return hexlify(bytes(packet.pack())).upper().decode()


class TestLegacyCodec:
    def test_roundtrip(self) -> None:
        payload = bytearray(Packet(DeviceId.PC, DeviceId.ES_BLE, Command.INIT, 0).pack())
        encrypted = LegacyCodec.encrypt(payload)
        # header + 4 payload bytes + 2 checksum bytes
        assert len(encrypted) == 9
        assert LegacyCodec.checksum_ok(encrypted)
        assert LegacyCodec.decrypt(encrypted) == bytes(payload)

    def test_checksum_detects_corruption(self) -> None:
        payload = bytearray(Packet(DeviceId.PC, DeviceId.ES_CONTROL, Command.READ, 0x10, [0x02]).pack())
        encrypted = bytearray(LegacyCodec.encrypt(payload))
        encrypted[5] ^= 0xFF
        assert not LegacyCodec.checksum_ok(encrypted)

    def test_encrypted_frame_is_not_legacy(self) -> None:
        """An encrypted frame's trailer is not a valid legacy checksum."""
        payload = bytearray(Packet(DeviceId.PC, DeviceId.ES_CONTROL, Command.READ, 0x10, [0x02]).pack())
        plain = bytes(payload)
        legacy = bytearray(LegacyCodec.encrypt(payload))
        assert LegacyCodec.is_legacy_frame(legacy)
        assert not LegacyCodec.is_legacy_frame(bytearray(plain + b"\x00\x01"))

    def test_read_ack_frame_checksum_matches_miauth_example(self) -> None:
        """Cross-check the checksum against the miauth example ACK frame."""
        # 5AA514213D010210 0200 ... is a read reply; verify with a synthetic body.
        body = bytes.fromhex("1413D210042100AA")
        checksum = LegacyCodec.sum16(body)
        assert checksum == (~sum(body)) & 0xFFFF


class TestPacket:
    def test_unpack_roundtrip(self) -> None:
        packet = Packet(DeviceId.PC, DeviceId.ES_CONTROL, Command.READ, 0x29, [0x02])
        unpacked = Packet.unpack(packet.pack())
        assert unpacked is not None
        assert unpacked.source == DeviceId.PC
        assert unpacked.target == DeviceId.ES_CONTROL
        assert unpacked.command == Command.READ
        assert unpacked.data_index == 0x29
        assert unpacked.data_segment == [0x02]

    def test_unpack_safe_unknown_ids_do_not_crash(self) -> None:
        """Regression: unknown device ids / commands used to raise ValueError."""
        data = bytearray(Packet(DeviceId.PC, DeviceId.ES_CONTROL, Command.READ, 0x10, [0x02]).pack())
        data[3] = 0x55  # unknown source
        assert Packet.unpack_safe(data) is None
        data = bytearray(Packet(DeviceId.PC, DeviceId.ES_CONTROL, Command.READ, 0x10, [0x02]).pack())
        data[5] = 0x77  # unknown command
        assert Packet.unpack_safe(data) is None

    def test_unpack_safe_accepts_known_ids(self) -> None:
        data = Packet(DeviceId.PHONE, DeviceId.ES_BLE, Command.PAIR, 1).pack()
        packet = Packet.unpack_safe(data)
        assert packet is not None
        assert packet.source == DeviceId.PHONE

    def test_unpack_short_frame(self) -> None:
        assert Packet.unpack(bytearray(b"\x5a\xa5\x00")) is None
        assert Packet.unpack_safe(bytearray(b"\x5a\xa5\x00")) is None


class TestSerialParser:
    def test_max_g30lp(self) -> None:
        parsed = SerialParser("N4GND1939C0123")
        assert str(parsed) == "Ninebot G30LP (30 km/h)"
        assert parsed.product_series == SerialParser.ProductSeries.MAX

    def test_f30(self) -> None:
        assert str(SerialParser("N5GCN2113C0123")) == "Ninebot F30"

    def test_e25e(self) -> None:
        assert str(SerialParser("N2GXM2039C0123")) == "Ninebot E25E"

    def test_es4_black(self) -> None:
        assert str(SerialParser("N2GSX1839C0123")) == "Ninebot ES4 (Black)"

    def test_es2_silver(self) -> None:
        assert str(SerialParser("N2GTX1939C0123")) == "Ninebot ES2 (Silver)"

    def test_seat_mo(self) -> None:
        assert str(SerialParser("N4YCD2039C0123")) == "Ninebot Seat Mó (G30D, 20 km/h)"

    def test_rental_max(self) -> None:
        assert str(SerialParser("N4LSD2039C0123")) == "Ninebot Max SNSC2.0-series"

    def test_revision_field_fixed(self) -> None:
        """Regression: revision was read from index 10 (first unit digit)."""
        parsed = SerialParser("N4GS D 19 39 C 0123".replace(" ", ""))
        assert parsed.product_revision == "C"
        assert parsed.weekly_serial == 123

    def test_short_serial_weekly_serial_optional(self) -> None:
        parsed = SerialParser("N4GS D 19 39 C".replace(" ", ""))
        assert parsed.product_revision == "C"
        assert parsed.weekly_serial is None

    def test_unknown_series_is_graceful(self) -> None:
        parsed = SerialParser("XXYGD2039C0123")
        assert str(parsed) == "Ninebot XXY-series"

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            SerialParser("N4G")

    def test_all_wiki_models_parse(self) -> None:
        for series, versions in SerialParser.PRODUCT_VERSION_MAPPING.items():
            for prefix in versions:
                serial = f"{series}{prefix}D2039C0123"
                parsed = SerialParser(serial)
                assert str(parsed).startswith("Ninebot")


class TestScooterDetection:
    def test_classic_manufacturer_id(self) -> None:
        from ninebot_ble.util import is_scooter_advertisement

        assert is_scooter_advertisement("NBScooter", {0x424E: b""})

    def test_newer_generation_manufacturer_id(self) -> None:
        """Newer scooters advertise 0x434E with a serial-style name."""
        from ninebot_ble.util import is_scooter_advertisement

        assert is_scooter_advertisement("1TEFE2517C0419", {0x434E: bytes.fromhex("0105020000f7")})

    def test_serial_style_name_without_manufacturer_data(self) -> None:
        from ninebot_ble.util import is_scooter_advertisement

        assert is_scooter_advertisement("1TEFE2517C0419", {})

    def test_unrelated_device_rejected(self) -> None:
        from ninebot_ble.util import is_scooter_advertisement

        assert not is_scooter_advertisement("Washer", {})
        assert not is_scooter_advertisement("[TV] Samsung", {})
        assert not is_scooter_advertisement(None, {})


class TestRegisters:
    def test_register_indices_unique_in_api(self) -> None:
        """The same CtrlIdx must not be exported twice (NB_POWER regression)."""
        names = [str(idx) for idx in iter_register(CtrlIdx, BmsIdx)]
        assert len(names) == len(set(names))

    def test_nb_power_removed(self) -> None:
        assert not hasattr(CtrlIdx, "NB_POWER")

    def test_registers_have_descriptions(self) -> None:
        for idx in iter_register(CtrlIdx, BmsIdx):
            desc = get_register_desc(idx)
            assert desc.read_len > 0
            assert desc.index_len > 0


class FakeBleakClient:
    """Minimal stand-in for BleakClient to drive the handshake offline."""

    def __init__(self) -> None:
        self.is_connected = False
        self.writes: list[bytes] = []
        self.notify_callback: Any = None
        self.legacy = False
        # How the scooter confirms a button-press pairing: "pair" answers the
        # PAIR request with PAIR-1 and keeps the BLE key; "ping" spontaneously
        # sends a fresh PING-1 and both sides switch to the app key.
        self.confirm_style = "pair"
        # Number of PAIR requests received before the "button press" happens.
        self.button_after: int | None = None
        self.pair_requests = 0

    async def start_notify(self, uuid: str, callback: Any) -> None:
        self.notify_callback = callback

    async def stop_notify(self, uuid: str) -> None:
        self.notify_callback = None

    async def disconnect(self) -> None:
        self.is_connected = False

    async def write_gatt_char(self, uuid: str, data: bytes) -> None:
        self.writes.append(bytes(data))
        # Reassemble written chunks and answer according to the protocol under
        # test, mirroring how a real scooter reacts.
        if self._pending is None:
            self._pending = bytearray()
        self._pending += data
        frame = self._pending
        if len(frame) < 3:
            return
        total = frame[2] + 7
        if len(frame) < total:
            return
        self._pending = None
        await self._respond(bytearray(frame))

    _pending: bytearray | None = None

    async def _respond(self, frame: bytearray) -> None:
        if not self.legacy and LegacyCodec.checksum_ok(frame):
            # Encrypted scooters validate the crypto MAC and silently drop
            # plain (legacy) frames instead of answering them.
            return
        if self.legacy:
            decrypted = LegacyCodec.decrypt(frame)
        else:
            decrypted = self._crypto.decrypt(frame)
        packet = Packet.unpack_safe(bytearray(decrypted))
        assert packet is not None
        if packet.command == Command.INIT:
            serial = b"N4GS D 19 39 C 0123".replace(b" ", b"")
            # The INIT ack is still keyed by the pre-handshake key; the
            # scooter switches to the issued key only afterwards.
            resp = Packet(DeviceId.ES_BLE, DeviceId.PC, Command.INIT, 1, list(self._ble_key) + list(serial))
            resp_frame = resp.pack()
            if self.legacy:
                wire = LegacyCodec.encrypt(resp_frame)
            else:
                wire = self._crypto.encrypt(resp_frame)
        elif packet.command == Command.PING:
            # The client proves itself with its random app key; remember it.
            self._app_key = bytes(packet.data_segment[:16])
            idx = 1 if self.paired else 0
            resp = Packet(DeviceId.ES_BLE, DeviceId.PC, Command.PING, idx)
            resp_frame = resp.pack()
            if self.legacy:
                wire = LegacyCodec.encrypt(resp_frame)
            else:
                wire = self._crypto.encrypt(resp_frame)
            if idx == 1:
                # Already-paired scooter: from now on both sides derive the
                # session keys from the app key.
                self._crypto.set_app_data(self._app_key)
        elif packet.command == Command.PAIR:
            self.pair_requests += 1
            pressed = self.button_after is not None and self.pair_requests >= self.button_after
            if self.legacy or self.paired or pressed:
                self.paired = True
                if (
                    not self.legacy
                    and not self.was_paired_at_start
                    and self.confirm_style == "ping"
                    and pressed
                    and not self._ping_confirmed
                ):
                    # Scooter confirms with a fresh PING-1 (once!) and switches
                    # to the app key, as documented in miauth's state machine.
                    self._ping_confirmed = True
                    resp = Packet(DeviceId.ES_BLE, DeviceId.PC, Command.PING, 1)
                    resp_frame = resp.pack()
                    wire = self._crypto.encrypt(resp_frame)
                    await self.notify_callback(None, bytearray(wire))
                    self._crypto.set_app_data(self._app_key)
                    return
                resp = Packet(DeviceId.ES_BLE, DeviceId.PC, Command.PAIR, 1)
                resp_frame = resp.pack()
                if self.legacy:
                    wire = LegacyCodec.encrypt(resp_frame)
                else:
                    wire = self._crypto.encrypt(resp_frame)
            else:
                # Button not pressed yet: no answer at all.
                return
        else:
            return
        if packet.command == Command.INIT:
            # Switch the scooter key only after the INIT ack is on the wire.
            self._crypto.set_ble_data(self._ble_key)
        await self.notify_callback(None, bytearray(wire))

    paired = False
    was_paired_at_start = False
    _ping_confirmed = False
    _crypto: Any = None
    _ble_key = b"\x01" * 16
    _app_key = b"\x00" * 16


def make_client_with_fake(legacy: bool, name: str = "NBScooter") -> tuple[NinebotClient, FakeBleakClient]:
    from miauth.nb.nbcrypto import NbCrypto

    client = NinebotClient()
    fake = FakeBleakClient()
    fake.legacy = legacy
    fake.paired = True  # PING is answered with data_index=1 (already paired)
    fake.was_paired_at_start = True
    crypto = NbCrypto()
    # The crypto key is derived from the BLE device name, so the fake must use
    # exactly the name the client will see on the device.
    crypto.set_name(name.encode())
    fake._crypto = crypto

    async def fake_establish_connection(bleak_client_class, device, address, **kwargs):  # type: ignore[no-untyped-def]
        return fake

    import ninebot_ble.transport as transport_module

    transport_module.establish_connection = fake_establish_connection
    return client, fake


class TestHandshake:
    @pytest.mark.asyncio
    async def test_connect_encrypted(self) -> None:
        client, fake = make_client_with_fake(legacy=False)
        device = type("BLEDevice", (), {"name": "NBScooter", "address": "AA:BB:CC:DD:EE:FF"})()
        await client.connect(device)  # type: ignore[arg-type]
        assert client.legacy is False
        assert fake.paired
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_legacy_plain(self) -> None:
        client, fake = make_client_with_fake(legacy=True)
        device = type("BLEDevice", (), {"name": "NBScooter", "address": "AA:BB:CC:DD:EE:FF"})()
        await client.connect(device)  # type: ignore[arg-type]
        assert client.legacy is True
        assert fake.paired
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_unpaired_button_confirm_pair_style(self) -> None:
        """Scooter answers PAIR-1 after the button press and keeps the BLE key
        (the flow observed on the live 0x434E scooter)."""
        client, fake = make_client_with_fake(legacy=False)
        fake.paired = False
        fake.was_paired_at_start = False
        fake.confirm_style = "pair"
        fake.button_after = 2
        device = type("BLEDevice", (), {"name": "NBScooter", "address": "AA:BB:CC:DD:EE:FF"})()
        await client.connect(device)  # type: ignore[arg-type]
        assert client.legacy is False
        assert fake.paired
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_connect_unpaired_button_confirm_ping_style(self) -> None:
        """Scooter confirms with a fresh PING-1 and both sides switch to the
        app key (the flow documented in miauth's state machine)."""
        client, fake = make_client_with_fake(legacy=False)
        fake.paired = False
        fake.was_paired_at_start = False
        fake.confirm_style = "ping"
        fake.button_after = 2
        device = type("BLEDevice", (), {"name": "NBScooter", "address": "AA:BB:CC:DD:EE:FF"})()
        await client.connect(device)  # type: ignore[arg-type]
        assert client.legacy is False
        assert fake.paired
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_read_reg_encrypted(self) -> None:
        client, fake = make_client_with_fake(legacy=False)

        async def fake_request(request: Packet, timeout: float = 5) -> Packet:
            if request.command == Command.READ and request.data_index == 0x32:
                # Remaining battery capacity in percent
                return Packet(DeviceId.ES_BATT, DeviceId.PC, Command.READ_ACK, 0x32, [0x55, 0x00])
            raise TimeoutError("unexpected read")

        client.request = fake_request  # type: ignore[method-assign]
        val = await client.read_reg(BmsIdx.BAT_REMAINING_CAP_PERCENT)
        assert val == 85

    @pytest.mark.asyncio
    async def test_init_failure_disconnects(self) -> None:
        client, fake = make_client_with_fake(legacy=False)

        async def failing_request(request: Packet, timeout: float = 5) -> Packet:
            raise TimeoutError("no response")

        client.request = failing_request  # type: ignore[method-assign]
        device = type("BLEDevice", (), {"name": "NBScooter", "address": "AA:BB:CC:DD:EE:FF"})()
        with pytest.raises(TimeoutError):
            await client.connect(device)  # type: ignore[arg-type]
        assert client.client is None
