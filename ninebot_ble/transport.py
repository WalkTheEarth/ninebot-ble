from __future__ import annotations

import asyncio
import enum
import logging
import secrets
import time
from binascii import hexlify
from struct import pack
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import establish_connection, retry_bluetooth_connection_error
from miauth.nb.nbcrypto import NbCrypto

from .register import BmsIdx, CtrlIdx, get_register_desc

_LOGGER = logging.getLogger(__name__)

NORDIC_UART_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NORDIC_UART_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

INIT_ACK_PAYLOAD_LEN = 24  # 16-byte BLE key + serial
PAIRING_TIMEOUT = 60.0  # seconds to wait for the user to press the power button


class Command(enum.Enum):
    READ = 0x01
    """Read control table data."""
    WRITE = 0x02
    """Write control table data, with reply."""
    WRITE_ACK_NO_REPLY = 0x03
    """Write control table data, without reply."""
    READ_ACK = 0x04
    """Response packet to instruction reading."""
    WRITE_ACK = 0x05
    """Response packet to instruction writing."""
    INIT = 0x5B
    PING = 0x5C
    PAIR = 0x5D

    @classmethod
    def from_maybe(cls, value: int) -> Command | None:
        """Return the Command for value, or None if it is not a known command."""
        try:
            return cls(value)
        except ValueError:
            return None


class DeviceId(enum.Enum):
    ES_CONTROL = 0x20
    """Master control of electric scooter (ES)"""
    ES_BLE = 0x21
    """Bluetooth instrument of ES"""
    ES_BATT = 0x22
    """Built-in battery of ES"""
    PC = 0x3D
    """PC upper computer connected through serial port/CAN debugger/IoT equipment"""
    PHONE = 0x3E
    """Mobile phone linked through Bluetooth serial port (BLE)"""

    @classmethod
    def from_maybe(cls, value: int) -> DeviceId | None:
        """Return the DeviceId for value, or None if it is not a known id.

        Some models and firmwares use device ids that are not in the list above.
        """
        try:
            return cls(value)
        except ValueError:
            return None


class Packet:
    MAGIC = [0x5A, 0xA5]
    """All packets sent to scooter must start with this preamble."""

    def __init__(
        self,
        source: DeviceId,
        target: DeviceId,
        command: Command,
        data_index: int,
        data: list[int] | bytes | None = None,
    ) -> None:
        self.source = source
        self.target = target
        self.command = command
        self.data_index = data_index
        self.data_segment = list(data) if data else []

    def pack(self) -> bytearray:
        payload = pack(
            "BBBBB", len(self.data_segment), self.source.value, self.target.value, self.command.value, self.data_index
        ) + bytes(self.data_segment)
        return bytearray(bytes(self.MAGIC) + payload)

    @staticmethod
    def unpack(data: bytearray) -> Packet | None:
        """Unpack a frame, raising on unknown device ids or commands."""
        if len(data) < 7 or list(data[:2]) != Packet.MAGIC:
            return None
        segment_len = data[2]
        if len(data) < 7 + segment_len:
            return None
        return Packet(DeviceId(data[3]), DeviceId(data[4]), Command(data[5]), data[6], list(data[7:]))

    @staticmethod
    def unpack_safe(data: bytearray) -> Packet | None:
        """Unpack a frame, returning None on malformed or unknown content.

        Unlike unpack() this never raises: scooters of different models and
        firmware generations send device ids and commands we do not know about,
        and those frames should be skipped instead of crashing the client.
        """
        if len(data) < 7 or list(data[:2]) != Packet.MAGIC:
            return None
        segment_len = data[2]
        if len(data) < 7 + segment_len:
            return None
        source = DeviceId.from_maybe(data[3])
        target = DeviceId.from_maybe(data[4])
        command = Command.from_maybe(data[5])
        if source is None or target is None or command is None:
            _LOGGER.debug("Skipping frame with unknown ids: %s", hexlify(bytes(data)).upper().decode())
            return None
        return Packet(source, target, command, data[6], list(data[7:]))


class LegacyCodec:
    """Plain (unencrypted) variant of the 0x5A 0xA5 protocol.

    Older scooter firmwares (early ES-series and friends) do not support the
    encrypted protocol; they exchange plain frames protected only by a 16-bit
    inverted-sum checksum appended after the payload.
    """

    @staticmethod
    def sum16(data: bytes | bytearray) -> int:
        """The legacy checksum: inverted 16-bit sum of all covered bytes."""
        return (~sum(data)) & 0xFFFF

    @classmethod
    def checksum_ok(cls, frame: bytearray) -> bool:
        if len(frame) < 9:
            return False
        expected = cls.sum16(frame[2:-2])
        actual = frame[-2] | (frame[-1] << 8)
        return expected == actual

    @classmethod
    def decrypt(cls, frame: bytearray) -> bytes:
        """Legacy frames are plain; just drop the checksum trailer."""
        return bytes(frame[:-2])

    @classmethod
    def encrypt(cls, payload: bytearray) -> bytes:
        checksum = cls.sum16(payload[2:])
        return bytes(payload) + bytes([checksum & 0xFF, (checksum >> 8) & 0xFF])

    @classmethod
    def is_legacy_frame(cls, frame: bytearray) -> bool:
        """True if frame looks like a legacy plain frame with valid checksum."""
        return cls.checksum_ok(frame)


class NinebotClient:
    APP_KEY = secrets.token_bytes(16)

    def __init__(self) -> None:
        self.crypto = NbCrypto()
        self.receive_queue: asyncio.Queue[Packet] = asyncio.Queue(100)
        self.receive_buffer = bytearray()
        self.client: BleakClient | None = None
        # None until the handshake tells us which protocol the scooter speaks.
        self.legacy: bool | None = None
        # Per-instance key so that two clients (e.g. two scooters) never share one.
        self.app_key = self.APP_KEY

    async def connect(self, device: BLEDevice) -> None:
        """Connect and handshake the scooter.

        This function must be called before any other.
        """
        self.crypto.set_name(device.name.encode() if device.name else b"Unnamed")

        _LOGGER.info("Connecting to %s (%s): ...", device.name, device.address)
        self.client = await establish_connection(BleakClient, device, device.address)
        await self.client.start_notify(NORDIC_UART_TX_UUID, self._read_callback)

        _LOGGER.debug("Authenticating ...")

        # Probe which protocol generation the scooter speaks: old firmwares
        # only understand plain frames (2-byte checksum trailer) and ignore
        # encrypted ones, so try plain first and fall back to encrypted.
        init_packet = Packet(DeviceId.PC, DeviceId.ES_BLE, Command.INIT, 0)
        try:
            self.legacy = True
            resp = await self.request(init_packet, timeout=3)
        except (TimeoutError, asyncio.TimeoutError):
            self.legacy = False
            try:
                resp = await self.request(init_packet, timeout=5)
            except (TimeoutError, asyncio.TimeoutError):
                await self.disconnect()
                raise
        received_key = resp.data_segment[:16]
        received_serial = resp.data_segment[16:]

        _LOGGER.debug("> Protocol: %s", "legacy plain" if self.legacy else "encrypted")
        _LOGGER.debug("> BLE Key: %s", hexlify(bytes(received_key)).upper().decode())
        _LOGGER.debug("> Serial: %s", bytes(received_serial).decode(errors="replace"))
        if not self.legacy:
            self.crypto.set_ble_data(received_key)

        if self.legacy:
            # Old firmwares only know the plain protocol; pair without crypto.
            await self.request(Packet(DeviceId.PC, DeviceId.ES_BLE, Command.PAIR, 0, received_serial))
            _LOGGER.debug("Connected successfully (legacy protocol)!")
            return

        # Ping
        resp = await self.request(Packet(DeviceId.PC, DeviceId.ES_BLE, Command.PING, 0, self.app_key))
        if resp.data_index == 0:
            # Zero (0) indicates we are not paired yet. The scooter stays in
            # this state until the user presses its power button.
            _LOGGER.info(
                "Scooter is not paired yet: press the power button on the "
                "scooter once to confirm pairing (waiting up to %d s) ...",
                int(PAIRING_TIMEOUT),
            )
            resp = None
            deadline = time.time() + PAIRING_TIMEOUT
            while resp is None and time.time() < deadline:
                await asyncio.sleep(1.0)
                # Sending pair request here seem to pair the device. Unclear why.
                await self.send(Packet(DeviceId.PC, DeviceId.ES_BLE, Command.PAIR, 0, received_serial))
                try:
                    resp = await self.receive()
                except TimeoutError:
                    pass
                if resp is None:
                    continue
                if resp.command == Command.PING and resp.data_index == 1:
                    self.crypto.set_app_data(self.app_key)
                    break
                if resp.command == Command.PAIR and resp.data_index == 1:
                    break
                resp = None
            if resp is None:
                await self.disconnect()
                raise TimeoutError(
                    "Scooter did not confirm pairing within "
                    f"{int(PAIRING_TIMEOUT)} s. Press the power button on the "
                    "scooter once right after connecting and try again."
                )

        # Pair
        await self.request(Packet(DeviceId.PC, DeviceId.ES_BLE, Command.PAIR, 0, received_serial))

        _LOGGER.debug("Connected and authenticated successfully!")

    async def disconnect(self) -> None:
        if self.client and self.client.is_connected:
            try:
                await self.client.stop_notify(NORDIC_UART_TX_UUID)
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
            await self.client.disconnect()
        self.client = None

    def _encrypt(self, payload: bytearray) -> bytes:
        if self.legacy:
            return LegacyCodec.encrypt(payload)
        return self.crypto.encrypt(payload)

    def _decrypt(self, frame: bytearray) -> bytes:
        if self.legacy is None:
            # Auto-detect the protocol from the first response frame.
            self.legacy = LegacyCodec.is_legacy_frame(frame)
            _LOGGER.debug("Scooter speaks the %s protocol", "legacy plain" if self.legacy else "encrypted")
        if self.legacy:
            return LegacyCodec.decrypt(frame)
        return self.crypto.decrypt(frame)

    @retry_bluetooth_connection_error()
    async def send(self, packet: Packet) -> None:
        """Send a BLE-UART packet to scooter."""
        assert self.client is not None, "Must be connected first."
        _LOGGER.debug("Sending %s", packet)
        msg = self._encrypt(packet.pack())
        msg_len = len(msg)
        byte_idx = 0
        while msg_len > 0:
            tmp_len = msg_len if msg_len <= 20 else 20
            buf = msg[byte_idx : byte_idx + tmp_len]
            _LOGGER.debug("Sending chuck %d/%d: %s", byte_idx + tmp_len, len(msg), hexlify(buf).upper().decode())
            await self.client.write_gatt_char(NORDIC_UART_RX_UUID, buf)
            msg_len -= tmp_len
            byte_idx += tmp_len

    @property
    def is_connected(self) -> bool:
        """Returns True if scooter is connected, otherwise False."""
        return self.client is not None and self.client.is_connected

    async def receive(self, timeout: float = 1) -> Packet:
        """Receive one BLE-UART packet from scooter."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.receive_queue.empty():
                await asyncio.sleep(0.1)
                continue
            return await self.receive_queue.get()
        raise TimeoutError("Timeout receiving packet")

    async def request(self, request: Packet, timeout: float = 5) -> Packet:
        """Sends request and returns matching response.

        Helper that combines send() and receive(). This function only works for some types of
        messages (e.g. register and symmetric send/receive packets).
        """
        command_replies = {Command.READ: Command.READ_ACK}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                await self.send(request)

                while time.time() < deadline:
                    recv_packet = await self.receive()
                    if recv_packet.command not in command_replies.values() and recv_packet.command != request.command:
                        # e.g. a stray notification; ignore frames we cannot match
                        if request.command not in (Command.INIT, Command.PING, Command.PAIR):
                            continue
                    if (
                        recv_packet.source == request.target
                        and recv_packet.target == request.source
                        and recv_packet.command == command_replies.get(request.command, request.command)
                        and (request.command.value > 0x5 or recv_packet.data_index == request.data_index)
                    ):
                        return recv_packet
                raise TimeoutError(f"Timeout waiting for response for: {request}")
            except TimeoutError:
                _LOGGER.debug("Retrying request ...")
        raise TimeoutError(f"Did not get a response on {request}")

    async def read_reg(self, index: CtrlIdx | BmsIdx) -> Any:
        """Read scooter memory register.

        Just tell which one and this function will do the rest.
        """
        if isinstance(index, CtrlIdx):
            target = DeviceId.ES_CONTROL
        else:
            target = DeviceId.ES_BATT

        reg = get_register_desc(index)

        data: list[int] = []
        for i in range(reg.index_len):
            resp = await self.request(Packet(DeviceId.PC, target, Command.READ, reg.index_start + i, [reg.read_len]))
            data.extend(resp.data_segment)

        unpacked = reg.unpacker(data)
        if reg.scaler:
            unpacked = reg.scaler(unpacked)
        return unpacked

    async def _read_callback(self, _: BleakGATTCharacteristic, data: bytearray) -> None:
        try:
            await self._handle_received(data)
        except Exception:  # noqa: BLE001 - never let the callback crash the connection
            _LOGGER.exception("Error while processing received BLE data")

    async def _handle_received(self, data: bytearray) -> None:
        # A frame starts with the 0x5A 0xA5 magic. Notifications can contain a
        # whole frame, a fragment, or (on some adapters) several concatenated
        # frames, so re-sync on 0xA5 instead of blindly appending.
        if len(data) >= 2 and list(data[:2]) == Packet.MAGIC:
            self.receive_buffer = bytearray(data)
        elif len(self.receive_buffer) == 1 and self.receive_buffer[0] == Packet.MAGIC[0] and data[:1] == b"\xa5":
            # The previous notification ended with a lone 0x5A; this one starts
            # with the second magic byte 0xA5.
            self.receive_buffer += data[1:]
        else:
            self.receive_buffer += data

        if len(self.receive_buffer) < 3:
            return

        decrypted = self._decrypt(self.receive_buffer)
        total_len = self.receive_buffer[2] + 7
        _LOGGER.debug(f"Decrypted {len(decrypted)}/{total_len}: {hexlify(decrypted).upper().decode()}")

        if len(decrypted) == total_len:
            self.receive_buffer = bytearray()
            packet = Packet.unpack_safe(decrypted)
            if packet is None:
                _LOGGER.warning("Failed to decode received packet")
                return
            # Drop stale packets when the queue backed up, so a poll never
            # reads a reply belonging to an earlier request.
            if self.receive_queue.full():
                try:
                    self.receive_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            await self.receive_queue.put(packet)
        elif len(decrypted) > total_len:
            # Several frames may arrive concatenated; re-sync on the next one.
            resync = decrypted.find(b"\xa5", 2)
            if resync != -1 and resync + 1 < len(self.receive_buffer):
                self.receive_buffer = self.receive_buffer[resync - 1 :]
            else:
                self.receive_buffer = bytearray()
                _LOGGER.warning(
                    "Malformed packet received, expected packet size %d bytes, received %d bytes",
                    total_len,
                    len(decrypted),
                )
