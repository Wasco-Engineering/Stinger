"""
Port abstraction - combines LabJack + Alicat for a single test port.

Each port (A/B, Left/Right) is an independent test station with its own
hardware and state machine.
"""

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from app.core.config import is_port_installed

from .labjack import LabJackController, TransducerReading, SwitchState
from .alicat import AlicatController, AlicatReading

logger = logging.getLogger(__name__)


class PortId(Enum):
    """Identifier for test ports."""
    PORT_A = "port_a"  # Left
    PORT_B = "port_b"  # Right


@dataclass
class PortReading:
    """Combined reading from all port hardware."""
    transducer: Optional[TransducerReading] = None
    switch: Optional[SwitchState] = None
    alicat: Optional[AlicatReading] = None
    dio: Optional[Dict[int, int]] = None
    timestamp: float = 0.0


@dataclass
class EdgeEvent:
    """Record of a switch edge detection."""
    pressure: float
    timestamp: float
    direction: str  # 'increasing' or 'decreasing'
    activated: bool  # True if switch became activated
    

# Nominal atmosphere for absolute-pressure safety check (PSI)
_ATMOSPHERE_PSI = 14.7


class Port:
    """Single test port with LabJack + Alicat hardware."""
    
    def __init__(
        self,
        port_id: PortId,
        labjack_config: Dict[str, Any],
        alicat_config: Dict[str, Any],
        solenoid_config: Optional[Dict[str, Any]] = None,
    ):
        """Initialize a test port."""
        self.port_id = port_id
        self._solenoid_config = solenoid_config or {}
        terminal_mode = labjack_config.get('use_ptp_terminals', False)
        if isinstance(terminal_mode, str):
            self._ptp_terminal_mode = terminal_mode.strip().lower()
        else:
            self._ptp_terminal_mode = 'true' if bool(terminal_mode) else 'false'
        self._configured_no_dio = self._optional_int(labjack_config.get('switch_no_dio'))
        self._configured_nc_dio = self._optional_int(labjack_config.get('switch_nc_dio'))
        self._configured_com_dio = self._optional_int(labjack_config.get('switch_com_dio'))

        # Initialize hardware controllers
        self.daq = LabJackController(labjack_config)
        self.alicat = AlicatController(alicat_config)
        
        # Edge detection state
        self._last_switch_state: Optional[SwitchState] = None
        self._edge_history: List[EdgeEvent] = []
        self._edge_callbacks: List[Callable[[EdgeEvent], None]] = []

        # Cached Alicat reading for fast polling (updated every Nth cycle)
        self._cached_alicat: Optional[AlicatReading] = None
        
        # Current test context
        self._no_pin: Optional[int] = None
        self._nc_pin: Optional[int] = None
        
        logger.info(f"Port {port_id.value} initialized")
    
    def configure_from_ptp(self, ptp_params: Dict[str, str]) -> bool:
        """Configure port hardware from PTP parameters."""
        try:
            use_ptp_terminals = self._should_use_ptp_terminals(ptp_params)
            if not use_ptp_terminals:
                logger.info(
                    'Port %s: Using configured NO/NC pins (PTP terminal override disabled)',
                    self.port_id.value,
                )
            else:
                # Extract terminal pin assignments
                no_terminal = ptp_params.get('NormallyOpenTerminal')
                nc_terminal = ptp_params.get('NormallyClosedTerminal')
                com_terminal = ptp_params.get('CommonTerminal')

                no_pin = self._terminal_to_dio(no_terminal)
                nc_pin = self._terminal_to_dio(nc_terminal)
                com_pin = self._terminal_to_dio(com_terminal)
                no_pin, nc_pin = self._apply_single_sense_terminal_preference(no_pin, nc_pin)

                if no_pin is not None or nc_pin is not None:
                    sense_pin = no_pin if no_pin is not None else nc_pin
                    if sense_pin is None:
                        logger.warning('Port %s: Missing valid switch sense pin in PTP', self.port_id.value)
                    else:
                        configured_no_pin = no_pin if no_pin is not None else sense_pin
                        configured_nc_pin = nc_pin if nc_pin is not None else sense_pin
                        derive_nc_from_no = no_pin is not None and nc_pin is None
                        derive_no_from_nc = nc_pin is not None and no_pin is None
                        logger.info(
                            'Port %s: Using PTP DB9 terminals COM=%s, NO=%s, NC=%s%s',
                            self.port_id.value,
                            self._format_terminal_mapping(com_terminal, com_pin),
                            self._format_terminal_mapping(no_terminal, no_pin),
                            self._format_terminal_mapping(nc_terminal, nc_pin),
                            ' (single-sense)' if derive_nc_from_no or derive_no_from_nc else '',
                        )
                        self._no_pin = configured_no_pin
                        self._nc_pin = configured_nc_pin
                        self.daq.switch_nc_derived_from_no = derive_nc_from_no
                        self.daq.switch_no_derived_from_nc = derive_no_from_nc
                        self.daq.configure_di_pins(
                            configured_no_pin,
                            configured_nc_pin,
                            com_pin,
                            com_state=self.daq.switch_com_state,
                        )
                else:
                    logger.warning('Port %s: Missing terminal pin assignments in PTP', self.port_id.value)
            
            logger.info(f"Port {self.port_id.value}: Configured from PTP")
            return True
            
        except Exception as e:
            logger.error(f"Port {self.port_id.value}: PTP configuration error: {e}")
            return False

    def _should_use_ptp_terminals(self, ptp_params: Dict[str, str]) -> bool:
        mode = self._ptp_terminal_mode
        if mode in {'true', 'yes', '1', 'on'}:
            return True
        if mode != 'auto':
            return False

        terminal_pairs = (
            (ptp_params.get('CommonTerminal'), self._configured_com_dio),
            (ptp_params.get('NormallyOpenTerminal'), self._configured_no_dio),
            (ptp_params.get('NormallyClosedTerminal'), self._configured_nc_dio),
        )
        has_usable_terminal = False
        for terminal, configured_dio in terminal_pairs:
            if not terminal:
                continue
            try:
                ptp_dio = self._map_db9_pin_to_dio(int(float(terminal)))
            except (TypeError, ValueError):
                continue
            if ptp_dio is None:
                continue
            has_usable_terminal = True
            if ptp_dio != configured_dio:
                return True
        if not has_usable_terminal:
            return False
        return False

    def _apply_single_sense_terminal_preference(
        self,
        no_pin: Optional[int],
        nc_pin: Optional[int],
    ) -> tuple[Optional[int], Optional[int]]:
        """Prefer the PTP terminal wired to this stand's single sense input."""
        configured_derived_from_no = bool(self.daq.switch_nc_derived_from_no)
        configured_derived_from_nc = bool(self.daq.switch_no_derived_from_nc)
        if not configured_derived_from_no and not configured_derived_from_nc:
            return no_pin, nc_pin

        configured_sense = (
            self._configured_no_dio if configured_derived_from_no else self._configured_nc_dio
        )
        if configured_sense is None:
            return no_pin, nc_pin
        if no_pin == configured_sense:
            return no_pin, None
        if nc_pin == configured_sense:
            return None, nc_pin
        return no_pin, nc_pin

    @staticmethod
    def _optional_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    def _terminal_to_dio(self, value: Any) -> Optional[int]:
        terminal = self._optional_int(value)
        if terminal is None:
            return None
        return self._map_db9_pin_to_dio(terminal)

    @staticmethod
    def _format_terminal_mapping(terminal: Any, dio: Optional[int]) -> str:
        if dio is None:
            return f'{terminal}->--'
        return f'{terminal}->DIO{dio}'

    def _map_db9_pin_to_dio(self, pin: int) -> Optional[int]:
        if pin < 1 or pin > 9:
            return None
        if self.port_id == PortId.PORT_A:
            return pin - 1
        return pin + 8
    
    def connect(self) -> bool:
        """
        Connect to all hardware for this port.
        
        Returns:
            True if all connections successful.
        """
        success = True
        
        # Configure LabJack
        if not self.daq.configure():
            logger.error(f"Port {self.port_id.value}: LabJack configuration failed")
            success = False
        
        # Connect to Alicat
        if not self.alicat.connect():
            logger.error(f"Port {self.port_id.value}: Alicat connection failed")
            success = False
        
        if success:
            logger.info(f"Port {self.port_id.value}: All hardware connected")
        
        return success
    
    def read_all(self) -> PortReading:
        """Read all sensors for this port."""
        return self._read(use_cached_alicat=False)
    
    def refresh_alicat(self) -> None:
        """Update the cached Alicat reading (slow serial I/O)."""
        self._cached_alicat = self.alicat.read_status()

    def read_fast(self) -> PortReading:
        """Read LabJack-only sensors (fast path) using cached Alicat.

        Reads transducer, switch state, and DIO from the LabJack but uses
        the most recently cached Alicat reading instead of blocking on serial.
        """
        return self._read(use_cached_alicat=True, include_dio=True)

    def read_precision_fast(self) -> PortReading:
        """Minimal LabJack read for precision sweep (transducer + switch only).

        Skips DIO_STATE to reduce shared T7 bus time while Alicat runs at the
        precision poll divisor on the same loop.
        """
        return self._read(use_cached_alicat=True, include_dio=False)

    def _read(self, use_cached_alicat: bool, include_dio: bool = True) -> PortReading:
        """Shared read path used by full, fast, and precision reads."""
        import time

        timestamp = time.time()
        alicat_reading = self._cached_alicat if use_cached_alicat else self.alicat.read_status()

        reading = PortReading(
            transducer=self.daq.read_transducer(),
            switch=self.daq.read_switch_state(),
            alicat=alicat_reading,
            dio=self.daq.read_dio_values(max_dio=22) if include_dio else None,
            timestamp=timestamp,
        )
        self._normalize_transducer_reference(reading)
        self._check_for_edge(reading)
        return reading

    def _normalize_transducer_reference(self, reading: PortReading) -> None:
        """Convert transducer absolute to gauge when LabJack is gauge-referenced."""
        # Convert transducer absolute -> gauge if configured
        if reading.transducer and reading.alicat:
            if getattr(self.daq, 'pressure_reference', 'absolute') == 'gauge':
                baro = reading.alicat.barometric_pressure
                if baro is not None:
                    reading.transducer.pressure = reading.transducer.pressure - baro
                    reading.transducer.pressure_reference = 'gauge'

    def _check_for_edge(self, reading: PortReading) -> None:
        """Check if a switch edge occurred and record it."""
        if reading.switch is None:
            return
        
        current = reading.switch
        previous = self._last_switch_state
        
        if previous is not None and current.switch_activated != previous.switch_activated:
            # Edge detected!
            pressure = self._physical_abs_pressure_psi(reading)
            if pressure is None:
                pressure = 0.0
            
            # Determine direction based on pressure change
            # (Would need to track pressure history for accurate direction)
            direction = "unknown"  # Will be set by state machine based on control direction
            
            edge = EdgeEvent(
                pressure=pressure,
                timestamp=current.timestamp,
                direction=direction,
                activated=current.switch_activated
            )
            
            self._edge_history.append(edge)
            logger.info(f"Port {self.port_id.value}: Edge detected at {pressure:.2f} PSI, "
                       f"activated={current.switch_activated}")
            
            # Notify callbacks
            for callback in self._edge_callbacks:
                try:
                    callback(edge)
                except Exception as e:
                    logger.error(f"Edge callback error: {e}")
        
        self._last_switch_state = current

    def _physical_abs_pressure_psi_for_solenoid_guard(self) -> tuple[Optional[float], float]:
        """Best-effort absolute pressure and barometric basis for vacuum-route safety."""
        transducer = self.daq.read_transducer()
        reading = PortReading(transducer=transducer, alicat=self._cached_alicat)
        self._normalize_transducer_reference(reading)
        pressure = self._physical_abs_pressure_psi(reading)
        if pressure is not None:
            barometric = _ATMOSPHERE_PSI
            if reading.alicat and reading.alicat.barometric_pressure is not None:
                barometric = float(reading.alicat.barometric_pressure)
            return pressure, barometric

        alicat_reading = self.alicat.read_status() or self._cached_alicat
        barometric = _ATMOSPHERE_PSI
        if alicat_reading and alicat_reading.barometric_pressure is not None:
            barometric = float(alicat_reading.barometric_pressure)
        return self._alicat_abs_pressure_psi(alicat_reading), barometric

    @staticmethod
    def _alicat_abs_pressure_psi(reading: Optional[AlicatReading]) -> Optional[float]:
        if reading is None:
            return None
        if reading.pressure is not None:
            return float(reading.pressure)
        if reading.gauge_pressure is None:
            return None
        barometric = reading.barometric_pressure
        if barometric is None:
            barometric = _ATMOSPHERE_PSI
        return float(reading.gauge_pressure + barometric)

    def _physical_abs_pressure_psi(self, reading: PortReading) -> Optional[float]:
        """Best-effort physical line pressure for edge history/logging."""
        if reading.transducer is not None:
            from app.services.measurement_source import _transducer_pressure_abs_psi

            barometric = (
                reading.alicat.barometric_pressure
                if reading.alicat and reading.alicat.barometric_pressure is not None
                else _ATMOSPHERE_PSI
            )
            pressure = _transducer_pressure_abs_psi(reading, float(barometric))
            if pressure is not None:
                return pressure
        return self._alicat_abs_pressure_psi(reading.alicat)
    
    def register_edge_callback(self, callback: Callable[[EdgeEvent], None]) -> None:
        """Register a callback to be called when an edge is detected."""
        self._edge_callbacks.append(callback)
    
    def clear_edge_history(self) -> None:
        """Clear the edge detection history."""
        self._edge_history.clear()
        self._last_switch_state = None
    
    def get_edge_history(self) -> List[EdgeEvent]:
        """Get the list of detected edges."""
        return self._edge_history.copy()
    
    def set_pressure(self, setpoint: float) -> bool:
        """Set the Alicat pressure setpoint."""
        return self.alicat.set_pressure(setpoint)
    
    def set_ramp_rate(self, rate: float) -> bool:
        """Set the Alicat ramp rate."""
        return self.alicat.set_ramp_rate(rate)
    
    def set_solenoid(self, to_vacuum: bool) -> bool:
        """Set the solenoid state.

        Pump protection: do not switch to vacuum unless port pressure is at or
        below the safe threshold (~atmosphere). Switching with high positive
        pressure can damage the pump.
        """
        if to_vacuum:
            # Only open to vacuum when pressure is close to atm (pump blowout protection).
            threshold_psi = self._solenoid_config.get(
                "safe_vacuum_switch_threshold_psi", 1.0
            )
            if threshold_psi is not None:
                pressure_psi, barometric = self._physical_abs_pressure_psi_for_solenoid_guard()
                safe_limit = barometric + float(threshold_psi)

                if pressure_psi is None or pressure_psi > safe_limit:
                    logger.warning(
                        "%s: Refusing vacuum - port pressure %.2f exceeds safe limit %.2f psi "
                        "(pump protection)",
                        self.port_id.value,
                        pressure_psi if pressure_psi is not None else -1.0,
                        safe_limit,
                    )
                    return False
        result = self.daq.set_solenoid(to_vacuum)
        if result:
            # Reset EMA filter so it re-seeds from the next sample after the
            # pressure discontinuity caused by the solenoid switch.
            self.daq.reset_filter()
        return result

    def connect_test_route(self) -> bool:
        """Connect the DUT to the Alicat-controlled test line.

        The energized solenoid state is named ``vacuum`` historically, but on
        this stand it is the active Alicat/test route for both positive-pressure
        and vacuum moves.  Callers should still use ``vent_to_atmosphere`` for
        the safe/idle state.
        """
        result = self.daq.set_solenoid(to_vacuum=True)
        if result:
            self.daq.reset_filter()
        return result

    def vent_to_atmosphere(self) -> bool:
        """Vent the port to atmosphere (safe state)."""
        # Set solenoid to atmosphere
        self.daq.set_solenoid_safe()
        # Reset filter after solenoid change
        self.daq.reset_filter()
        self.alicat.cancel_hold()
        ok = self.alicat.exhaust()
        try:
            self.refresh_alicat()
        except Exception:
            pass
        return ok

    def prepare_vacuum_route_for_test(self, barometric_psi: float = _ATMOSPHERE_PSI) -> bool:
        """Vent on atmosphere, then route to vacuum for test cycling (transducer-guarded)."""
        self.vent_to_atmosphere()
        transducer = self.daq.read_transducer()
        if transducer is not None and transducer.pressure is not None:
            from app.services.measurement_source import _transducer_pressure_abs_psi

            tr_reading = PortReading(transducer=transducer, alicat=self._cached_alicat)
            self._normalize_transducer_reference(tr_reading)
            transducer_psi = _transducer_pressure_abs_psi(tr_reading, barometric_psi)
            if transducer_psi is not None and transducer_psi > barometric_psi + 3.0:
                logger.warning(
                    "%s: Vacuum prep with elevated line pressure %.2f psia (proceeding)",
                    self.port_id.value,
                    transducer_psi,
                )
        if not self.daq.set_solenoid(to_vacuum=True):
            return False
        self.daq.reset_filter()
        return True
    
    def disconnect(self, *, restore_safe_state: bool = True) -> None:
        """Disconnect all hardware.

        When ``restore_safe_state`` is False, leave solenoid routing unchanged so
        the physical line stays on vacuum after the app exits (leak-down tests).
        """
        if restore_safe_state:
            self.daq.set_solenoid_safe()
        try:
            self.alicat.hold_valve()
        except Exception:
            pass

        self.daq.cleanup(preserve_solenoid_state=not restore_safe_state)
        self.alicat.disconnect()
        
        logger.info(f"Port {self.port_id.value}: Disconnected")
    
    def get_status(self) -> Dict[str, Any]:
        """Get combined status of all hardware."""
        return {
            "port_id": self.port_id.value,
            "daq": self.daq.get_status(),
            "alicat": self.alicat.get_status(),
        }


class PortManager:
    """Manages test ports (A and B)."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize port manager."""
        self.config = config
        self.ports: Dict[PortId, Port] = {}
        self._polling = False
        self._poll_thread: Optional[threading.Thread] = None
        timing_cfg = config.get('timing', {})
        self._poll_interval_ms = timing_cfg.get('hardware_poll_interval_ms', 10)
        legacy_divisor = max(1, int(timing_cfg.get('alicat_poll_divisor', 10)))
        self._alicat_poll_divisor_normal = max(
            1, int(timing_cfg.get('alicat_poll_divisor_normal', legacy_divisor))
        )
        self._alicat_poll_divisor_precision = max(
            1, int(timing_cfg.get('alicat_poll_divisor_precision', self._alicat_poll_divisor_normal))
        )
        self._labjack_poll_divisor_sibling = max(
            1, int(timing_cfg.get('labjack_poll_divisor_sibling', self._alicat_poll_divisor_normal))
        )
        self._poll_interval_ms_precision = int(timing_cfg.get('hardware_poll_interval_ms_precision', 0))
        self._poll_callback: Optional[Callable[[Dict[PortId, PortReading]], None]] = None
        self._poll_policy_lock = threading.Lock()
        self._alicat_poll_divisors: Dict[PortId, int] = {}
        self._alicat_refresh_countdown: Dict[PortId, int] = {}
        self._precision_owner: Optional[PortId] = None
        self._labjack_sibling_countdown: Dict[PortId, int] = {}
        self._last_poll_readings: Dict[PortId, PortReading] = {}
        self._hardware_ready = False

        logger.info("PortManager initialized")

    @property
    def is_hardware_ready(self) -> bool:
        """True after start_polling(); live reads use poll_once() on the GUI thread."""
        return self._hardware_ready
    
    def initialize_ports(self) -> bool:
        """Initialize all configured ports."""
        labjack_config = self.config.get('hardware', {}).get('labjack', {})
        alicat_config = self.config.get('hardware', {}).get('alicat', {})

        success = True

        def build_labjack_config(port_key: str) -> Dict[str, Any]:
            # Start with all top-level (non-port) keys from hardware.labjack
            base = {
                key: value
                for key, value in labjack_config.items()
                if key not in {'port_a', 'port_b'}
            }
            # Overlay port-specific keys
            return {**base, **labjack_config.get(port_key, {})}

        def build_alicat_config(port_key: str) -> Dict[str, Any]:
            port_config = alicat_config.get(port_key, {})
            base_config = {
                key: value
                for key, value in alicat_config.items()
                if key not in {'port_a', 'port_b'}
            }
            return {**base_config, **port_config}

        solenoid_config = self.config.get("hardware", {}).get("solenoid", {})

        # Initialize Port A
        if 'port_a' in labjack_config and is_port_installed(self.config, 'port_a'):
            port_a = Port(
                port_id=PortId.PORT_A,
                labjack_config=build_labjack_config('port_a'),
                alicat_config=build_alicat_config('port_a'),
                solenoid_config=solenoid_config,
            )
            self.ports[PortId.PORT_A] = port_a

        # Initialize Port B
        if 'port_b' in labjack_config and is_port_installed(self.config, 'port_b'):
            port_b = Port(
                port_id=PortId.PORT_B,
                labjack_config=build_labjack_config('port_b'),
                alicat_config=build_alicat_config('port_b'),
                solenoid_config=solenoid_config,
            )
            self.ports[PortId.PORT_B] = port_b
        
        logger.info(f"PortManager: {len(self.ports)} ports initialized")
        with self._poll_policy_lock:
            for port_id in self.ports.keys():
                self._alicat_poll_divisors[port_id] = self._alicat_poll_divisor_normal
                self._alicat_refresh_countdown[port_id] = 0
        return success
    
    def connect_all(self) -> bool:
        """Connect to hardware for all ports.

        When an Alicat connection fails on its configured COM port, auto-
        discovery is attempted: available serial ports are probed for a
        responding Alicat at the expected address.  If found, the port's
        Alicat controller is updated and the connection retried.
        """
        import time

        success = True
        overall_start = time.perf_counter()
        for port_id, port in self.ports.items():
            port_start = time.perf_counter()
            if not port.connect():
                alicat = getattr(port, 'alicat', None)
                if alicat is None or not alicat.hardware_available() or alicat._is_connected:
                    logger.error(f"PortManager: Failed to connect {port_id.value}")
                    success = False
                else:
                    discovered = self._discover_alicat_port(port)
                    if discovered and discovered != alicat.com_port:
                        logger.info(
                            'PortManager: Auto-discovered %s Alicat on %s (was %s)',
                            port_id.value,
                            discovered,
                            alicat.com_port,
                        )
                        alicat.com_port = discovered
                        if not alicat.connect():
                            logger.error(f"PortManager: Failed to connect {port_id.value}")
                            success = False
                    else:
                        logger.error(f"PortManager: Failed to connect {port_id.value}")
                        success = False
            logger.info(
                "PortManager: %s connect completed in %.3fs",
                port_id.value,
                time.perf_counter() - port_start,
            )

        logger.info(
            'PortManager: connect_all finished in %.3fs (success=%s)',
            time.perf_counter() - overall_start,
            success,
        )
        
        return success

    def _discover_alicat_port(self, port: Port) -> Optional[str]:
        """Scan available serial ports for a responding Alicat at the expected address."""
        available = AlicatController.list_available_ports()
        available_ports = [p['device'] for p in available]
        if not available_ports:
            return None

        alicat = port.alicat
        for candidate in available_ports:
            if candidate == alicat.com_port:
                continue
            probe_cfg = {
                **alicat.config,
                'com_port': candidate,
                'auto_configure': False,
                'auto_tare_on_connect': False,
                'command_retries': 0,
                'response_read_attempts': 2,
            }
            probe = AlicatController(probe_cfg)
            try:
                if not probe.connect(max_retries=1):
                    continue
                reading = probe.read_status()
                if reading is not None:
                    return candidate
            except Exception as exc:
                logger.debug(
                    'Alicat discovery probe failed on %s address=%s: %s',
                    candidate,
                    alicat.address,
                    exc,
                )
            finally:
                try:
                    probe.disconnect()
                except Exception:
                    pass
        return None
    
    def get_port(self, port_id: PortId | str) -> Optional[Port]:
        """Get a specific port by ID."""
        if isinstance(port_id, str):
            try:
                port_id = PortId(port_id)
            except ValueError:
                return None
        return self.ports.get(port_id)
    
    def read_all_ports(self) -> Dict[PortId, PortReading]:
        """Read all sensors from all ports."""
        readings = {}
        for port_id, port in self.ports.items():
            readings[port_id] = port.read_all()
        return readings
    
    def disconnect_all(self, *, restore_safe_state: bool = True) -> None:
        """Disconnect all ports."""
        for port_id, port in list(self.ports.items()):
            port.disconnect(restore_safe_state=restore_safe_state)
        self.ports.clear()
        logger.info("PortManager: All ports disconnected")
    
    def get_all_status(self) -> Dict[str, Any]:
        """Get status of all ports."""
        return {
            port_id.value: port.get_status()
            for port_id, port in self.ports.items()
        }
    
    def set_poll_callback(self, callback: Callable[[Dict[PortId, PortReading]], None]) -> None:
        """Set callback function to be called with readings on each poll."""
        self._poll_callback = callback

    def set_alicat_poll_divisor(self, port_id: PortId | str, divisor: int) -> bool:
        """Set Alicat poll divisor for a single port at runtime."""
        normalized = self._normalize_port_id(port_id)
        if normalized is None:
            return False
        divisor_val = max(1, int(divisor))
        with self._poll_policy_lock:
            self._alicat_poll_divisors[normalized] = divisor_val
            # Apply speed increases immediately.
            self._alicat_refresh_countdown[normalized] = 0
        logger.info(
            'PortManager: %s Alicat poll divisor set to %d',
            normalized.value,
            divisor_val,
        )
        return True

    def set_alicat_poll_profile(self, precision_port: Optional[PortId | str]) -> None:
        """
        Apply precision polling profile for Alicat serial and LabJack transducer.

        When a precision port is active:
        - precision owner: Alicat divisor=precision, LabJack every cycle (no DIO)
        - sibling port(s): Alicat divisor=normal, LabJack every sibling divisor cycles
        """
        precision_id = self._normalize_port_id(precision_port) if precision_port is not None else None
        with self._poll_policy_lock:
            self._precision_owner = precision_id
            for port_id in self.ports.keys():
                if precision_id is not None and port_id == precision_id:
                    self._alicat_poll_divisors[port_id] = self._alicat_poll_divisor_precision
                else:
                    self._alicat_poll_divisors[port_id] = self._alicat_poll_divisor_normal
                # Force immediate refresh after profile change.
                self._alicat_refresh_countdown[port_id] = 0
                self._labjack_sibling_countdown[port_id] = 0
        if precision_id is None:
            logger.info(
                'PortManager: Poll profile normal (alicat_div=%d, labjack every cycle)',
                self._alicat_poll_divisor_normal,
            )
        else:
            logger.info(
                'PortManager: Precision poll owner=%s (alicat precision=%d normal=%d, '
                'labjack sibling_div=%d, interval_ms=%d)',
                precision_id.value,
                self._alicat_poll_divisor_precision,
                self._alicat_poll_divisor_normal,
                self._labjack_poll_divisor_sibling,
                self._poll_interval_ms_precision,
            )

    def get_precision_poll_status(self) -> Dict[str, Any]:
        """Return precision polling profile state for UI/diagnostics."""
        with self._poll_policy_lock:
            owner = self._precision_owner.value if self._precision_owner is not None else None
            return {
                'precision_owner': owner,
                'labjack_poll_divisor_sibling': self._labjack_poll_divisor_sibling,
                'hardware_poll_interval_ms_precision': self._poll_interval_ms_precision,
            }

    def get_alicat_poll_divisors(self) -> Dict[str, int]:
        """Return current per-port Alicat poll divisors."""
        with self._poll_policy_lock:
            return {
                port_id.value: int(self._alicat_poll_divisors.get(port_id, self._alicat_poll_divisor_normal))
                for port_id in self.ports.keys()
            }
    
    def start_polling(self) -> bool:
        """Enable hardware reads (polled on the Qt GUI thread via poll_once)."""
        if not self.ports:
            logger.error("PortManager: No ports initialized, cannot start polling")
            return False

        self._seed_alicat_cache()
        self._hardware_ready = True
        logger.info(
            "PortManager: Live hardware polling enabled (GUI thread, interval target=%sms)",
            self._poll_interval_ms,
        )
        return True

    def stop_polling(self) -> None:
        """Disable hardware reads."""
        self._hardware_ready = False
        if self._polling:
            self._polling = False
            if self._poll_thread:
                self._poll_thread.join(timeout=1.0)
                self._poll_thread = None
        logger.info("PortManager: Stopped polling")

    def _seed_alicat_cache(self) -> None:
        """Prime Alicat caches so the first GUI poll has serial data."""
        for port_id, port in self.ports.items():
            try:
                port.refresh_alicat()
            except Exception:
                pass
            with self._poll_policy_lock:
                divisor = int(self._alicat_poll_divisors.get(port_id, self._alicat_poll_divisor_normal))
                self._alicat_refresh_countdown[port_id] = max(0, divisor - 1)

    def poll_once(self, *, labjack_only: bool = False) -> Dict[PortId, PortReading]:
        """Read all ports once. Must run on the Qt main thread for reliable UI updates.

        When ``labjack_only`` is True, skip Alicat serial I/O (transducer + switch only).
        Use this while a background test thread owns the Alicat lock so the UI
        timer is not blocked for hundreds of milliseconds.
        """
        if not self._hardware_ready or not self.ports:
            return {}
        try:
            return self._collect_poll_readings(labjack_only=labjack_only)
        except Exception as exc:
            logger.error("PortManager: poll_once failed: %s", exc, exc_info=True)
            return {}

    def _collect_poll_readings(self, *, labjack_only: bool = False) -> Dict[PortId, PortReading]:
        """Single poll cycle: refresh Alicat when due, then read LabJack (+ cached Alicat)."""
        if not labjack_only:
            for port_id, port in self.ports.items():
                should_refresh = False
                with self._poll_policy_lock:
                    remaining = int(self._alicat_refresh_countdown.get(port_id, 0))
                    if remaining <= 0:
                        should_refresh = True
                        divisor = int(
                            self._alicat_poll_divisors.get(port_id, self._alicat_poll_divisor_normal)
                        )
                        self._alicat_refresh_countdown[port_id] = max(0, divisor - 1)
                    else:
                        self._alicat_refresh_countdown[port_id] = remaining - 1
                if should_refresh:
                    try:
                        port.refresh_alicat()
                    except Exception as exc:
                        logger.warning(
                            "PortManager: Alicat refresh failed for %s: %s",
                            port_id.value,
                            exc,
                        )

        readings: Dict[PortId, PortReading] = {}
        for port_id, port in self.ports.items():
            readings[port_id] = self._poll_reading(port_id, port)
        return readings
    
    def _poll_reading(self, port_id: PortId, port: Port) -> PortReading:
        """Read one port according to the active precision poll profile."""
        with self._poll_policy_lock:
            owner = self._precision_owner

        if owner is None:
            reading = port.read_fast()
        elif port_id == owner:
            reading = port.read_precision_fast()
        else:
            # Always read LabJack (transducer + switch); Alicat stays on the
            # per-port refresh divisor in _poll_loop. Reusing a cached PortReading
            # here froze the main UI pressure display on the non-precision port.
            reading = port.read_fast()

        self._last_poll_readings[port_id] = reading
        return reading

    def start_background_polling(self) -> bool:
        """Optional legacy background poll thread (not used for live UI)."""
        if self._polling:
            return False
        if not self.ports:
            return False
        self._polling = True
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        return True

    def _poll_loop(self) -> None:
        """Legacy background poll loop; live UI uses poll_once() on the GUI thread."""
        import time

        self._seed_alicat_cache()
        while self._polling:
            start_time = time.perf_counter()
            with self._poll_policy_lock:
                precision_active = self._precision_owner is not None
            interval_ms = (
                self._poll_interval_ms_precision
                if precision_active
                else self._poll_interval_ms
            )
            if precision_active and interval_ms <= 0:
                interval_ms = self._poll_interval_ms
            interval_s = max(0.001, interval_ms / 1000.0)

            try:
                readings = self._collect_poll_readings()
                if self._poll_callback and readings:
                    self._poll_callback(readings)
            except Exception as e:
                logger.error("PortManager: Polling error: %s", e)

            elapsed = time.perf_counter() - start_time
            sleep_time = max(0.0, interval_s - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    @staticmethod
    def _normalize_port_id(port_id: PortId | str | None) -> Optional[PortId]:
        if port_id is None:
            return None
        if isinstance(port_id, PortId):
            return port_id
        try:
            return PortId(str(port_id))
        except ValueError:
            return None
