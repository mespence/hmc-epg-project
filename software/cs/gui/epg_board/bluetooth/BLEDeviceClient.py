import asyncio
from dataclasses import dataclass
from typing import Optional, Callable
from bleak import BleakClient


@dataclass
class Timeouts:
    """Per-attempt time budgets (seconds) for connect -> discover -> subscribe."""
    connect: float = 10.0
    discover: float = 5.0 # NOTE: currently unused as we know the exact UUIDs
    subscribe: float = 5.0


class BLEDeviceClient:
    """
    Thin async wrapper around BleakClient. 
    Call from within an asyncio event loop (the BLE worker loop).
    """

    def __init__(
        self,
        address: str,
        *,
        notify_uuid: str,
        write_uuid: str,
        timeouts: Timeouts,
    ) -> None:
        self._address = address
        self._notify_uuid = notify_uuid
        self._write_uuid = write_uuid
        self._timeouts = timeouts

        self._client: Optional[BleakClient] = None
        self._notify_started = False

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def connect(self) -> None:
        """Connect to the device; raises on failure."""
        self._client = BleakClient(self._address)
        await asyncio.wait_for(self._client.connect(), timeout=self._timeouts.connect)


    async def start_notifications(self, callback: Callable[[bytes], None]) -> None:
        """
        Start notifications on the notify characteristic.

        `callback` is called (in the event loop thread) as callback(data: bytes).
        """
        if not self._client or not self._client.is_connected:
            raise RuntimeError("start_notifications called while not connected")

        def _on_notify(_sender: int, data: bytes) -> None:
            # All work is delegated to FrameParser.
            callback(data)

        await asyncio.wait_for(
            self._client.start_notify(self._notify_uuid, _on_notify),
            timeout=self._timeouts.subscribe,
        )
        self._notify_started = True

    async def stop_notifications(self) -> None:
        if self._client and self._client.is_connected and self._notify_started:
            try:
                await self._client.stop_notify(self._notify_uuid)
            finally:
                self._notify_started = False

    async def write(self, payload: bytes, *, response: bool) -> None:
        """
        Write to the write characteristic.

        - response=True  -> await ACK from stack
        - response=False -> fire-and-forget at GATT level
        """
        if not self._client or not self._client.is_connected:
            raise RuntimeError("write called while not connected")

        await self._client.write_gatt_char(self._write_uuid, payload, response=response)

    async def disconnect(self) -> None:
        """Stop notifications (if any) and disconnect the client."""
        client = self._client
        self._client = None

        if not client:
            return

        try:
            if client.is_connected and self._notify_started:
                try:
                    await client.stop_notify(self._notify_uuid)
                except Exception:
                    pass
        finally:
            self._notify_started = False

        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass

