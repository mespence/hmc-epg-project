from dataclasses import dataclass
from typing import Tuple, List

@dataclass
class DataFrame:
    """Single ADC data sample from the device."""
    timestamp_ms: int
    millivolts: int


@dataclass
class ManagementFrame:
    """Non-data line (status, error, debug, etc.)."""
    payload: str


class BLEFrameParser:
    """
    Accumulates raw bytes over BLE connection, splits into CRLF-terminated 
    lines, and classifies each line as a DataFrame (timestamp, mV) or 
    a ManagementFrame (everything else).
    """

    def __init__(self, *, line_separator: bytes = b"\r\n") -> None:
        self._buffer = bytearray()
        self._separator = line_separator

    def feed(self, data: bytes) -> None:
        """Append raw bytes from the BLE notify callback."""
        self._buffer.extend(data)

    def take_frames(self) -> Tuple[List[DataFrame], List[ManagementFrame]]:
        """
        Consume all complete lines currently in the buffer, leaving any trailing
        partial line for the next call.

        Returns:
            (data_frames, management_frames)
        """
        if not self._buffer:
            return [], []

        data = bytes(self._buffer)
        last_idx = data.rfind(self._separator)
        if last_idx == -1: # No complete CRLF yet; keep data as-is.
            return [], []  

        complete_region = data[: last_idx + len(self._separator)]
        leftover = data[last_idx + len(self._separator) :]
        self._buffer = bytearray(leftover)

        raw_lines = complete_region.split(self._separator)
        lines = [line for line in raw_lines if line]

        data_frames: List[DataFrame] = []
        mgmt_frames: List[ManagementFrame] = []

        malformed_count = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Heuristic for data vs management:
            # Data line: "12345,678"
            parts = stripped.split(b",", 1)
            if (
                len(parts) == 2
                and parts[0].isdigit()
                and (parts[1].lstrip(b"+-").isdigit())
            ):
                try:
                    ts = int(parts[0])
                    mv = int(parts[1])
                    data_frames.append(DataFrame(timestamp_ms=ts, millivolts=mv))
                    continue
                except ValueError:
                    malformed_count += 1
                    continue

            # Otherwise treat as management
            try:
                text = stripped.decode("utf-8", errors="replace")
            except Exception:
                text = "<decode-error>"
            mgmt_frames.append(ManagementFrame(payload=text))

        return data_frames, mgmt_frames
