"""Minimal Mensor serial reader for the quality calibration workflow."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    import serial

    SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - hardware dependency
    serial = None
    SERIAL_AVAILABLE = False


@dataclass(slots=True)
class MensorReading:
    pressure_psia: float
    timestamp: float


class MensorReader:
    """Simple serial client for a Mensor pressure reference."""

    # Keep last N raw responses for diagnostic logging (tail of readings).
    _RESPONSE_TAIL_SIZE = 20

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._port = str(config.get("port", "COM10"))
        self._baudrate = int(config.get("baudrate", 57600))
        self._timeout_s = float(config.get("timeout_s", 1.0))
        self._serial = None
        self._last_status = "Not Connected"
        self._response_tail: list[str] = []

    @property
    def status(self) -> str:
        return self._last_status

    @property
    def response_tail(self) -> list[str]:
        """Last N raw serial responses for diagnostic logging."""
        return list(self._response_tail)

    def connect(self) -> bool:
        if not SERIAL_AVAILABLE:
            self._last_status = "Connected (simulated)"
            return True

        try:
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baudrate,
                bytesize=8,
                parity=serial.PARITY_NONE,
                stopbits=1,
                timeout=self._timeout_s,
            )
            time.sleep(0.3)
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            for command in ("MODE MEASURE",):
                self._send(command)
            self._last_status = "Connected"
            return True
        except Exception as exc:  # pragma: no cover - hardware dependency
            self._last_status = f"Error: {exc}"
            logger.error("Failed to connect Mensor: %s", exc)
            self.close()
            return False

    def close(self) -> None:
        try:
            if self._serial:
                self._serial.close()
        except Exception:
            pass
        self._serial = None
        if self._last_status != "Connected (simulated)":
            self._last_status = "Disconnected"

    def read_pressure(self) -> MensorReading:
        if not SERIAL_AVAILABLE:
            return MensorReading(pressure_psia=14.7, timestamp=time.time())

        response = self._send("?")
        pressure = self._parse_pressure(response)
        if pressure is None:
            raise RuntimeError("Mensor read_pressure failed")
        # Log when reading is far from typical cal range (possible wrong field or unit)
        if not (0.0 <= pressure <= 300.0):
            logger.warning(
                "Mensor raw response out of range: pressure=%.3f psia, response=%r",
                pressure,
                response[:200] if response else None,
            )
        return MensorReading(pressure_psia=pressure, timestamp=time.time())

    def _send(self, command: str) -> Optional[str]:
        if self._serial is None:
            return None
        try:
            self._serial.reset_input_buffer()
            self._serial.write(f"{command}\r".encode())
            self._serial.flush()
            time.sleep(0.05)
            response = self._serial.read_all().decode(errors="ignore").strip()
            if response:
                self._response_tail.append(response)
                if len(self._response_tail) > self._RESPONSE_TAIL_SIZE:
                    self._response_tail.pop(0)
            return response or None
        except Exception as exc:  # pragma: no cover - hardware dependency
            logger.error("Mensor communication error: %s", exc)
            return None

    @staticmethod
    def _parse_pressure(response: Optional[str]) -> Optional[float]:
        if not response:
            return None
        fields = [f.strip() for f in response.split(",") if f.strip()]
        # Scientific notation field (e.g. +1.34419E+01 psia) — common Mensor MEASURE response.
        for field in fields:
            if re.search(r"[Ee][+-]?\d+", field):
                try:
                    value = float(field)
                    if 0.01 <= value <= 300.0:
                        return value
                except ValueError:
                    pass
        # Legacy: field prefixed with E+ / E- only (value after prefix).
        for field in fields:
            upper = field.upper()
            if upper.startswith("E+") or upper.startswith("E-"):
                try:
                    value = float(field[2:].strip())
                    if 0.1 <= value <= 300.0:
                        return value
                except ValueError:
                    pass
        first_field = fields[0] if fields else ""
        try:
            value = float(first_field)
        except ValueError:
            match = re.search(r"[+-]?\d*\.?\d+(?:[Ee][+-]?\d+)?", first_field)
            if not match:
                return None
            value = float(match.group())

        # Heuristic for non-scientific numeric fields only (Pa/mbar legacy paths).
        if "e" not in first_field.lower():
            if value > 100.0:
                return value * 0.0001450377
            if value > 10.0:
                return value * 0.01450377
        return value

    @staticmethod
    def list_available_ports() -> list[str]:
        if not SERIAL_AVAILABLE or serial is None:
            return []
        tools_module = getattr(serial, "tools", None)
        if tools_module is None:
            return []
        list_ports_module = getattr(tools_module, "list_ports", None)
        if list_ports_module is None:
            return []
        try:
            return [port.device for port in list_ports_module.comports()]
        except Exception as exc:  # pragma: no cover - hardware dependency
            logger.error("Failed to enumerate Mensor serial ports: %s", exc)
            return []
