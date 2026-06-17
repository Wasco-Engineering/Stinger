"""
Cycle and Precision Test Script for Port A (15018 seq 399)

This script performs a full cycling and precision test sequence optimized for speed
while maintaining quality. It cycles 3 times to remove hysteresis, then performs
a precision sweep at 5 torr/s to find activation and deactivation points.

Usage:
    python scripts/cycle_precision_test.py
"""

from __future__ import annotations

import csv
import logging
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import load_config, setup_logging
from app.database.session import initialize_database
from app.hardware.port import Port, PortId, PortReading
from app.services.control_config import parse_control_config
from app.services.ptp_service import (
    TestSetup,
    convert_pressure,
    derive_test_setup,
    load_ptp_from_db,
    validate_ptp_params,
)
from app.services.sweep_primitives import (
    DebounceState,
    EdgeDetection,
    SweepResult,
    observe_debounced_transition,
    resolve_sweep_result,
)
from app.services.sweep_utils import resolve_sweep_bounds, resolve_sweep_mode

logger = logging.getLogger(__name__)

# Test parameters (hardcoded for this specific test)
PART_ID = '15018'
SEQUENCE_ID = '399'
PORT_ID = 'port_a'


@dataclass
class CycleResult:
    """Results from a single cycle."""
    cycle_num: int
    activation_estimate: Optional[float]
    deactivation_estimate: Optional[float]
    duration_s: float


@dataclass
class TestResults:
    """Complete test results."""
    part_id: str
    sequence_id: str
    port_id: str
    timestamp: str
    cycles: List[CycleResult]
    precision_activation_psi: Optional[float]
    precision_deactivation_psi: Optional[float]
    total_duration_s: float
    success: bool
    baseline_activation_psi: Optional[float] = None
    baseline_deactivation_psi: Optional[float] = None
    activation_delta_psi: Optional[float] = None
    deactivation_delta_psi: Optional[float] = None
    baseline_duration_s: float = 0.0
    optimized_duration_s: float = 0.0
    error_message: Optional[str] = None


class SimplifiedTestExecutor:
    """Simplified test executor for standalone testing."""
    
    def __init__(
        self,
        port: Port,
        test_setup: TestSetup,
        config: Dict[str, Any],
        get_latest_reading: callable,
        get_barometric_psi: callable,
    ):
        self._port = port
        self._test_setup = test_setup
        self._config = config
        self._get_latest_reading = get_latest_reading
        self._get_barometric_psi = get_barometric_psi
        
        # Parse control config
        control_cfg = parse_control_config(config)
        self._num_cycles = control_cfg.cycling.num_cycles
        self._slow_edge_rate_torr = control_cfg.ramps.precision_sweep_rate_torr_per_sec
        self._slow_edge_rate_psi = max(
            0.01,
            convert_pressure(self._slow_edge_rate_torr, 'Torr', 'PSI'),
        )
        
        # Fast rate for cycling (use maximum safe rate)
        # Default to 50 PSI/s if not configured
        self._fast_rate_psi = 50.0
        
        # Optimization flags
        self._use_optimized_approach = True
        self._minimal_atmosphere_hold = True
        
        # Edge detection config
        self._overshoot_pct = control_cfg.edge_detection.overshoot_beyond_limit_percent
        self._edge_timeout_s = control_cfg.edge_detection.timeout_sec
        self._atmosphere_tolerance_psi = control_cfg.edge_detection.atmosphere_tolerance_psi
        self._precision_approach_tolerance_torr = control_cfg.edge_detection.precision_approach_tolerance_torr
        self._precision_approach_tolerance_psi = max(
            0.02,
            convert_pressure(self._precision_approach_tolerance_torr, 'Torr', 'PSI'),
        )
        self._precision_approach_settle_s = control_cfg.edge_detection.precision_approach_settle_sec
        self._precision_atmosphere_hold_s = control_cfg.edge_detection.precision_start_atmosphere_hold_sec
        self._precision_close_limit_offset_torr = control_cfg.edge_detection.precision_close_limit_offset_torr
        self._precision_close_limit_offset_psi = max(
            0.05,
            convert_pressure(self._precision_close_limit_offset_torr, 'Torr', 'PSI'),
        )
        self._precision_prepass_nudge_torr = control_cfg.edge_detection.precision_prepass_nudge_torr
        self._precision_prepass_nudge_psi = max(
            0.02,
            convert_pressure(self._precision_prepass_nudge_torr, 'Torr', 'PSI'),
        )
        self._precision_deactivation_margin_torr = control_cfg.edge_detection.precision_deactivation_margin_torr
        self._precision_deactivation_margin_psi = max(
            0.02,
            convert_pressure(self._precision_deactivation_margin_torr, 'Torr', 'PSI'),
        )
        
        # Debounce config
        self._stable_count = control_cfg.debounce.stable_sample_count
        self._min_edge_interval_s = control_cfg.debounce.min_edge_interval_ms / 1000.0
        
        # Cycle edge collection
        self._cycle_activation_samples: List[float] = []
        self._cycle_deactivation_samples: List[float] = []
        self._cycle_debounce_state = DebounceState()
        
        # Atmosphere reference
        self._run_atmosphere_psi: Optional[float] = None
        
        # Cancel event (not used in standalone but kept for compatibility)
        class CancelEvent:
            def is_set(self):
                return False
        self._cancel_event = CancelEvent()
    
    def _to_absolute(self, test_psi: float) -> float:
        """Convert test reference pressure to absolute."""
        if self._test_setup and str(self._test_setup.pressure_reference or '').strip().lower() == 'absolute':
            return test_psi
        # Gauge reference - add atmosphere
        if self._run_atmosphere_psi is None:
            self._run_atmosphere_psi = self._get_barometric_psi(PORT_ID)
        return test_psi + self._run_atmosphere_psi
    
    def _absolute_to_test_reference(self, abs_psi: float) -> float:
        """Convert absolute pressure to test reference."""
        if self._test_setup and str(self._test_setup.pressure_reference or '').strip().lower() == 'absolute':
            return abs_psi
        # Gauge reference - subtract atmosphere
        if self._run_atmosphere_psi is None:
            self._run_atmosphere_psi = self._get_barometric_psi(PORT_ID)
        return abs_psi - self._run_atmosphere_psi
    
    def _determine_atmosphere_psi(self) -> float:
        """Determine atmosphere pressure based on reference."""
        pressure_ref = self._test_setup.pressure_reference if self._test_setup else None
        if str(pressure_ref or '').strip().lower() == 'absolute':
            if self._run_atmosphere_psi is not None:
                return self._run_atmosphere_psi
            return self._get_barometric_psi(PORT_ID)
        return 0.0
    
    def _resolve_sweep_mode(self) -> str:
        """Determine sweep mode."""
        return resolve_sweep_mode(
            self._test_setup,
            atmosphere_psi=self._determine_atmosphere_psi(),
        )
    
    def _resolve_sweep_bounds(self) -> Tuple[float, float]:
        """Get sweep bounds."""
        labjack_cfg = self._config.get('hardware', {}).get('labjack', {})
        port_cfg = labjack_cfg.get(PORT_ID, {})
        return resolve_sweep_bounds(self._test_setup, port_cfg)
    
    def _resolve_activation_sweep_direction(self) -> int:
        """Resolve activation direction."""
        direction = (self._test_setup.activation_direction or '').strip().lower() if self._test_setup else ''
        if direction.startswith('decreas') or direction in {'down', 'falling'}:
            return -1
        return 1
    
    def _set_pressure_or_raise(self, abs_psi: float) -> None:
        """Set pressure setpoint."""
        if not self._port.set_pressure(abs_psi):
            raise RuntimeError(f"Failed to set pressure setpoint to {abs_psi:.4f} PSI")
    
    def _effective_switch_state(self, switch_state: Any) -> bool:
        """Extract effective switch state."""
        if switch_state is None:
            return False
        if getattr(switch_state, 'is_valid', True):
            return bool(switch_state.switch_activated)
        return bool(getattr(switch_state, 'no_active', False))
    
    def _observe_cycle_switch_sample(self, pressure_test_psi: float, switch_state: Any) -> None:
        """Observe switch state during cycling to collect edge estimates."""
        if switch_state is None:
            return
        current = self._effective_switch_state(switch_state)
        self._cycle_debounce_state, committed_state, committed_pressure = observe_debounced_transition(
            self._cycle_debounce_state,
            current,
            self._stable_count,
            self._min_edge_interval_s,
            time.perf_counter(),
            track_last_sample=False,
            update_edge_time_on_reject=False,
            current_pressure=pressure_test_psi,
        )
        if committed_state is None:
            return
        activated = bool(committed_state)
        
        # Use first-detection pressure for accuracy
        sample_pressure = committed_pressure if committed_pressure is not None else pressure_test_psi
        if activated:
            self._cycle_activation_samples.append(sample_pressure)
        else:
            self._cycle_deactivation_samples.append(sample_pressure)
    
    def _mean_or_none(self, values: List[float]) -> Optional[float]:
        """Calculate mean or None."""
        if not values:
            return None
        return sum(values) / len(values)
    
    def _reading_pressure_abs_psi(self, reading: PortReading) -> Optional[float]:
        """Extract absolute pressure from reading."""
        if reading is None or reading.transducer is None:
            return None
        # Port.read_all() already converts to gauge if configured, so we need to check
        # If it's gauge, convert back to absolute by adding barometric
        pressure = reading.transducer.pressure
        pressure_ref = getattr(reading.transducer, 'pressure_reference', None)
        if str(pressure_ref or '').strip().lower() == 'gauge':
            # Convert gauge back to absolute
            if reading.alicat and reading.alicat.barometric_pressure is not None:
                return pressure + reading.alicat.barometric_pressure
            # Fallback: use stored barometric
            if self._run_atmosphere_psi is None:
                self._run_atmosphere_psi = self._get_barometric_psi(PORT_ID)
            return pressure + self._run_atmosphere_psi
        # Already absolute
        return pressure
    
    def _extract_pressures(
        self,
        reading: Optional[PortReading],
        collect_cycle_edges: bool = False,
    ) -> Tuple[Optional[float], Optional[float]]:
        """Extract pressures and optionally collect cycle edges."""
        if reading is None or reading.transducer is None:
            return None, None
        
        # Get the pressure as it comes from Port.read_all() (may already be gauge)
        raw_pressure = reading.transducer.pressure
        pressure_ref = getattr(reading.transducer, 'pressure_reference', None)
        
        # Determine if we need to convert
        if str(pressure_ref or '').strip().lower() == 'gauge':
            # Port already converted to gauge, so this is test reference pressure
            pressure_test = raw_pressure
            # Convert to absolute for internal use
            if reading.alicat and reading.alicat.barometric_pressure is not None:
                pressure_abs = raw_pressure + reading.alicat.barometric_pressure
            else:
                if self._run_atmosphere_psi is None:
                    self._run_atmosphere_psi = self._get_barometric_psi(PORT_ID)
                pressure_abs = raw_pressure + self._run_atmosphere_psi
        else:
            # Port gave us absolute, convert to test reference
            pressure_abs = raw_pressure
            pressure_test = self._absolute_to_test_reference(pressure_abs)
        
        if collect_cycle_edges:
            self._observe_cycle_switch_sample(pressure_test, reading.switch)
        return pressure_abs, pressure_test
    
    def _wait_for_atmosphere(
        self,
        atmosphere_psi: float,
        timeout_s: float,
        collect_cycle_edges: bool = False,
        hold_s: float = 0.0,
    ) -> bool:
        """Wait for port to reach atmosphere."""
        threshold_psi = max(0.05, self._atmosphere_tolerance_psi)
        start = time.perf_counter()
        near_since: Optional[float] = None
        last_log_time = start
        no_reading_count = 0
        
        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False
            
            reading = self._get_latest_reading(PORT_ID)
            pressure_abs, pressure_test = self._extract_pressures(reading, collect_cycle_edges)
            if pressure_test is None:
                no_reading_count += 1
                # Log every 5 seconds if no readings
                if time.perf_counter() - last_log_time >= 5.0:
                    logger.warning('%s: Waiting for atmosphere - no pressure reading (attempt %d)', PORT_ID, no_reading_count)
                    last_log_time = time.perf_counter()
                time.sleep(0.05)
                continue
            
            no_reading_count = 0  # Reset counter on successful reading
            
            # Log progress every 2 seconds
            elapsed = time.perf_counter() - start
            if elapsed - (last_log_time - start) >= 2.0:
                logger.debug(
                    '%s: Waiting for atmosphere: pressure=%.4f target=%.4f diff=%.4f tol=%.3f elapsed=%.1fs',
                    PORT_ID,
                    pressure_test,
                    atmosphere_psi,
                    abs(pressure_test - atmosphere_psi),
                    threshold_psi,
                    elapsed,
                )
                last_log_time = time.perf_counter()
            
            if abs(pressure_test - atmosphere_psi) <= threshold_psi:
                now = time.perf_counter()
                if near_since is None:
                    near_since = now
                    logger.info(
                        '%s: Atmosphere reached pressure=%.4f target=%.4f tol=%.3f hold=%.2fs',
                        PORT_ID,
                        pressure_test,
                        atmosphere_psi,
                        threshold_psi,
                        hold_s,
                    )
                if now - near_since >= max(0.0, hold_s):
                    if hold_s > 0.0:
                        logger.info('%s: Atmosphere hold complete (%.2fs)', PORT_ID, hold_s)
                    return True
            else:
                near_since = None
            
            time.sleep(0.05)
        
        logger.warning(
            '%s: Timeout (%.1fs) waiting for atmosphere target=%.4f tol=%.3f',
            PORT_ID,
            timeout_s,
            atmosphere_psi,
            threshold_psi,
        )
        return False
    
    def _wait_for_target(
        self,
        target_psi: float,
        direction: int,
        timeout_s: float,
        collect_cycle_edges: bool = False,
    ) -> bool:
        """Wait until pressure reaches target."""
        start = time.perf_counter()
        target_abs = self._to_absolute(target_psi)
        
        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False
            
            reading = self._get_latest_reading(PORT_ID)
            pressure_abs, pressure_test = self._extract_pressures(
                reading,
                collect_cycle_edges=collect_cycle_edges,
            )
            if pressure_abs is None:
                time.sleep(0.02)
                continue
            if direction > 0 and pressure_abs >= target_abs:
                return True
            if direction < 0 and pressure_abs <= target_abs:
                return True
            
            time.sleep(0.02)
        
        return False
    
    def _wait_until_near_target(
        self,
        target_psi: float,
        timeout_s: float,
        tolerance_psi: float,
        settle_s: float,
    ) -> bool:
        """Wait until pressure is near target and settled."""
        start = time.perf_counter()
        target_abs = self._to_absolute(target_psi)
        near_since: Optional[float] = None
        
        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False
            
            reading = self._get_latest_reading(PORT_ID)
            pressure_abs, _pressure_test = self._extract_pressures(reading, False)
            if pressure_abs is None:
                time.sleep(0.02)
                continue
            
            if abs(pressure_abs - target_abs) <= tolerance_psi:
                now = time.perf_counter()
                if near_since is None:
                    near_since = now
                if now - near_since >= settle_s:
                    return True
            else:
                near_since = None
            
            time.sleep(0.02)
        
        return False
    
    def _read_pressure_and_switch_state(self) -> Tuple[Optional[float], Optional[bool]]:
        """Read current pressure and switch state."""
        reading = self._get_latest_reading(PORT_ID)
        if reading is None:
            return None, None
        pressure_abs, pressure_test = self._extract_pressures(reading, False)
        switch_state = self._effective_switch_state(reading.switch) if reading.switch else None
        return pressure_test, switch_state
    
    def _resolve_hardware_limits_test_reference(self) -> Tuple[float, float]:
        """Get hardware limits in test reference."""
        labjack_cfg = self._config.get('hardware', {}).get('labjack', {})
        port_cfg = labjack_cfg.get(PORT_ID, {})
        min_psi = float(port_cfg.get('transducer_pressure_min', 0.0))
        max_psi = float(port_cfg.get('transducer_pressure_max', 115.0))
        return min_psi, max_psi
    
    def run_single_cycle(self, sweep_mode: str, bounds: Tuple[float, float]) -> None:
        """Run a single cycle - optimized to ramp between activation/deactivation directly."""
        min_psi, max_psi = bounds
        direction = 1 if sweep_mode == 'pressure' else -1
        hw_min_psi, hw_max_psi = self._resolve_hardware_limits_test_reference()
        
        # OPTIMIZED: Cycle directly between activation and deactivation
        # No need to go to atmosphere between cycles - just ramp up/down
        
        # Set solenoid route
        if not self._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            raise RuntimeError(f'Failed to set solenoid route for {sweep_mode}')
        
        # Set fast ramp rate
        if not self._port.alicat.set_ramp_rate(self._fast_rate_psi):
            raise RuntimeError(f'Failed to set fast ramp rate')
        
        # Ramp UP until activation detected (for increasing) or DOWN until activation (for decreasing)
        # Use a target well past the expected activation point
        if direction > 0:
            # Increasing: ramp up past activation
            target_activation = min(max_psi + 1.0, hw_max_psi)  # Go 1 PSI past max or to hardware limit
        else:
            # Decreasing: ramp down past activation
            target_activation = max(min_psi - 1.0, hw_min_psi)  # Go 1 PSI below min or to hardware limit
        
        target_activation_abs = self._to_absolute(target_activation)
        self._set_pressure_or_raise(target_activation_abs)
        self._port.alicat.cancel_hold()
        
        # Wait for activation edge - continue sampling to let debounce system commit
        activation_samples_before = len(self._cycle_activation_samples)
        start_time = time.perf_counter()
        edge_committed = False
        
        while time.perf_counter() - start_time < self._edge_timeout_s:
            reading = self._get_latest_reading(PORT_ID)
            if reading and reading.switch:
                pressure_abs, pressure_test = self._extract_pressures(reading, collect_cycle_edges=True)
                if pressure_test is not None:
                    # Check if debounce system committed an activation edge
                    if len(self._cycle_activation_samples) > activation_samples_before:
                        edge_committed = True
                        # Continue sampling briefly to ensure edge is fully committed
                        time.sleep(0.05)
                        break
            time.sleep(0.01)
        
        if not edge_committed:
            logger.warning('Activation edge not committed during cycle ramp up')
        
        # Immediately reverse direction - ramp DOWN until deactivation detected
        if direction > 0:
            # Increasing: now ramp down to deactivation
            target_deactivation = max(min_psi - 1.0, hw_min_psi)
        else:
            # Decreasing: now ramp up to deactivation
            target_deactivation = min(max_psi + 1.0, hw_max_psi)
        
        target_deactivation_abs = self._to_absolute(target_deactivation)
        self._set_pressure_or_raise(target_deactivation_abs)
        # Keep fast rate
        self._port.alicat.cancel_hold()
        
        # Wait for deactivation edge - continue sampling to let debounce system commit
        deactivation_samples_before = len(self._cycle_deactivation_samples)
        start_time = time.perf_counter()
        edge_committed = False
        
        while time.perf_counter() - start_time < self._edge_timeout_s:
            reading = self._get_latest_reading(PORT_ID)
            if reading and reading.switch:
                pressure_abs, pressure_test = self._extract_pressures(reading, collect_cycle_edges=True)
                if pressure_test is not None:
                    # Check if debounce system committed a deactivation edge
                    if len(self._cycle_deactivation_samples) > deactivation_samples_before:
                        edge_committed = True
                        # Continue sampling briefly to ensure edge is fully committed
                        time.sleep(0.05)
                        break
            time.sleep(0.01)
        
        if not edge_committed:
            logger.warning('Deactivation edge not committed during cycle ramp down')
        
        # Cycle complete - no need to return to atmosphere here
        # (We'll return to atmosphere after all cycles are done)
    
    def _sweep_to_edge(
        self,
        target_psi: float,
        direction: int,
    ) -> Optional[EdgeDetection]:
        """Sweep toward target, returning the first stable edge detected."""
        target_abs = self._to_absolute(target_psi)
        self._set_pressure_or_raise(target_abs)
        
        reading_start = self._get_latest_reading(PORT_ID)
        dynamic_timeout_s = self._edge_timeout_s
        rate_psi_per_sec = self._slow_edge_rate_psi
        if reading_start is not None and reading_start.transducer is not None:
            start_abs = self._reading_pressure_abs_psi(reading_start)
            if start_abs is None:
                start_abs = reading_start.transducer.pressure
            travel_psi = abs(target_abs - start_abs)
            estimated_travel_s = travel_psi / max(1e-4, rate_psi_per_sec)
            dynamic_timeout_s = max(self._edge_timeout_s, estimated_travel_s * 1.35 + 8.0)
        
        start = time.perf_counter()
        debounce_state = DebounceState()
        target_reached_since: Optional[float] = None
        # Optimized: reduce settle window for speed (edge detection stops immediately anyway)
        settle_window_s = max(0.05, self._stable_count * 0.015) if self._use_optimized_approach else max(0.08, self._stable_count * 0.02)
        
        while time.perf_counter() - start < dynamic_timeout_s:
            if self._cancel_event.is_set():
                return None
            
            reading = self._get_latest_reading(PORT_ID)
            if reading is None or reading.switch is None or reading.transducer is None:
                time.sleep(0.01)
                continue
            
            pressure_abs = self._reading_pressure_abs_psi(reading)
            if pressure_abs is None:
                time.sleep(0.01)
                continue
            pressure = self._absolute_to_test_reference(pressure_abs)
            current_state = self._effective_switch_state(reading.switch)
            
            debounce_state, committed_state, committed_pressure = observe_debounced_transition(
                debounce_state,
                current_state,
                self._stable_count,
                self._min_edge_interval_s,
                time.perf_counter(),
                track_last_sample=True,
                update_edge_time_on_reject=True,
                current_pressure=pressure,
            )
            if committed_state is not None and math.isfinite(pressure):
                edge_pressure = committed_pressure if committed_pressure is not None else pressure
                return EdgeDetection(pressure_psi=edge_pressure, activated=committed_state)
            
            reached_target = False
            if direction > 0 and pressure_abs >= target_abs:
                reached_target = True
            if direction < 0 and pressure_abs <= target_abs:
                reached_target = True
            
            if reached_target:
                now = time.perf_counter()
                if target_reached_since is None:
                    target_reached_since = now
                elif now - target_reached_since >= settle_window_s:
                    break
            else:
                target_reached_since = None
            
            time.sleep(0.01)
        
        return None
    
    def _execute_out_back_sweep(
        self,
        target_out: float,
        target_back: float,
        direction: int,
        rate_psi_per_sec: float,
        deactivation_estimate: Optional[float] = None,
    ) -> Optional[SweepResult]:
        """Execute out-and-back precision sweep with optimized rapid return."""
        if not self._port.alicat.set_ramp_rate(rate_psi_per_sec):
            logger.error('Failed to set sweep ramp rate')
            return None
        
        # Slow sweep to activation
        edge_out = self._sweep_to_edge(target_out, direction)
        if self._cancel_event.is_set():
            return None
        if edge_out is None:
            logger.warning('No first edge detected in precision out-sweep')
            return None
        
        # OPTIMIZATION: After detecting activation, rapidly ramp to just past deactivation estimate
        # then slow sweep back to find deactivation precisely
        if self._use_optimized_approach and deactivation_estimate is not None and direction > 0:
            # Get current pressure (should be at activation)
            current_reading = self._get_latest_reading(PORT_ID)
            if current_reading and current_reading.transducer:
                current_abs = self._reading_pressure_abs_psi(current_reading)
                if current_abs is not None:
                    current_test = self._absolute_to_test_reference(current_abs)
                    # Rapidly ramp to just past deactivation estimate (0.1 PSI past for safety)
                    rapid_target = max(target_back, deactivation_estimate - 0.1)
                    rapid_target_abs = self._to_absolute(rapid_target)
                    
                    logger.info(
                        '%s: Rapid return from activation (%.4f PSI) to past deactivation (%.4f PSI)',
                        PORT_ID,
                        current_test,
                        rapid_target,
                    )
                    
                    # Set fast ramp rate and target
                    if self._port.alicat.set_ramp_rate(self._fast_rate_psi):
                        self._set_pressure_or_raise(rapid_target_abs)
                        self._port.alicat.cancel_hold()
                        
                        # Wait briefly for rapid approach (should be very quick)
                        rapid_start = time.perf_counter()
                        while time.perf_counter() - rapid_start < 5.0:  # Max 5s for rapid ramp
                            reading = self._get_latest_reading(PORT_ID)
                            if reading and reading.transducer:
                                pressure_abs = self._reading_pressure_abs_psi(reading)
                                if pressure_abs is not None:
                                    pressure_test = self._absolute_to_test_reference(pressure_abs)
                                    # Check if we've passed deactivation (switch should be deactivated)
                                    if pressure_test <= rapid_target + 0.1:
                                        break
                            time.sleep(0.01)
                        
                        # Now slow sweep back to find deactivation precisely
                        logger.info('%s: Slow sweep back to find deactivation precisely', PORT_ID)
                        if not self._port.alicat.set_ramp_rate(rate_psi_per_sec):
                            logger.error('Failed to set slow ramp rate for return sweep')
                            return None
        
        # Slow sweep back to find deactivation
        edge_back = self._sweep_to_edge(target_back, -direction)
        if self._cancel_event.is_set():
            return None
        if edge_back is None:
            logger.warning('Second edge not detected in precision return-sweep')
            return None
        
        return resolve_sweep_result(edge_out, edge_back)
    
    def _resolve_precision_targets(
        self,
        min_psi: float,
        max_psi: float,
        activation_direction: int,
    ) -> Tuple[float, float, float, str]:
        """Resolve precision approach/out/back targets using cycle estimates."""
        activation_estimate = self._mean_or_none(self._cycle_activation_samples)
        deactivation_estimate = self._mean_or_none(self._cycle_deactivation_samples)
        
        if activation_estimate is not None and deactivation_estimate is not None:
            lower_est = min(activation_estimate, deactivation_estimate)
            upper_est = max(activation_estimate, deactivation_estimate)
            
            # OPTIMIZED: Start from below deactivation and sweep through both edges
            # This ensures we always detect edges and minimizes travel distance
            if self._use_optimized_approach:
                # MAXIMUM OPTIMIZATION: Minimize all margins for speed
                activation_range = abs(activation_estimate - deactivation_estimate)
                # Ultra-small margin past edges - just enough to ensure detection (0.05 PSI)
                sweep_past_margin_psi = 0.05  # Go only 0.05 PSI past edge before reversing
                # Approach margin - start further from deactivation to avoid starting too close to activation
                # Increased from 0.05 PSI to 0.3 PSI minimum to provide more safety margin
                approach_margin_psi = max(0.3, activation_range * 0.05)  # 5% margin, min 0.3 PSI
                
                if activation_direction < 0:
                    # Decreasing: start from above activation, sweep down
                    approach_target = min(max_psi, activation_estimate + approach_margin_psi)
                    target_out = max(min_psi, deactivation_estimate - sweep_past_margin_psi)
                    target_back = min(max_psi, activation_estimate + approach_margin_psi)
                else:
                    # Increasing: start from BELOW deactivation, sweep UP through both edges
                    # Start with increased margin below deactivation to avoid starting too close to activation
                    approach_target = max(min_psi, deactivation_estimate - approach_margin_psi)
                    # Out: sweep UP past activation - ultra-minimal margin (0.05 PSI)
                    # Since sweep stops immediately on edge detection, this is just a safety limit
                    target_out = activation_estimate + sweep_past_margin_psi
                    # Only clamp if it's way beyond reasonable bounds
                    if target_out > max_psi + 0.3:  # Allow up to 0.3 PSI past max_psi if needed
                        target_out = max_psi
                    # Back: sweep DOWN past deactivation - ultra-minimal margin (0.05 PSI)
                    target_back = max(min_psi, deactivation_estimate - sweep_past_margin_psi)
                
                logger.info(
                    '%s: Optimized precision targets: approach=%.4f (below deactivation) out=%.4f (past activation) back=%.4f (past deactivation)',
                    PORT_ID,
                    approach_target,
                    target_out,
                    target_back,
                )
                return (approach_target, target_out, target_back, 'cycle-estimate-optimized')
            
            # Non-optimized: use original conservative offsets
            offset = self._precision_close_limit_offset_psi
            margin = self._precision_deactivation_margin_psi
            if activation_direction < 0:
                close_limit = min(
                    max_psi,
                    max(min_psi, activation_estimate + offset, deactivation_estimate + margin),
                )
                target_out = lower_est
                approach_target = close_limit
                target_back = close_limit
            else:
                close_limit = max(
                    min_psi,
                    min(max_psi, activation_estimate - offset, deactivation_estimate - margin),
                )
                target_out = upper_est
                approach_target = close_limit
                target_back = close_limit
            return (approach_target, target_out, target_back, 'cycle-estimate-offset-close-limit')
        
        # Fallback to PTP bands or bounds
        if self._test_setup:
            if activation_direction < 0:
                activation_band = self._band_limits_to_psi(
                    self._test_setup.bands.get('decreasing'),
                    min_psi,
                    max_psi,
                )
                deactivation_band = self._band_limits_to_psi(
                    self._test_setup.bands.get('increasing'),
                    min_psi,
                    max_psi,
                )
                if activation_band and deactivation_band:
                    act_low, _act_high = activation_band
                    _deact_low, deact_high = deactivation_band
                    if deact_high > act_low:
                        close_limit = deact_high
                        return (close_limit, act_low, close_limit, 'ptp-close-limit')
            else:
                activation_band = self._band_limits_to_psi(
                    self._test_setup.bands.get('increasing'),
                    min_psi,
                    max_psi,
                )
                deactivation_band = self._band_limits_to_psi(
                    self._test_setup.bands.get('decreasing'),
                    min_psi,
                    max_psi,
                )
                if activation_band and deactivation_band:
                    _act_low, act_high = activation_band
                    deact_low, _deact_high = deactivation_band
                    if act_high > deact_low:
                        close_limit = deact_low
                        return (close_limit, act_high, close_limit, 'ptp-close-limit')
        
        if activation_direction < 0:
            return (max_psi, min_psi, max_psi, 'bounds-close-limit')
        return (min_psi, max_psi, min_psi, 'bounds-close-limit')
    
    def _band_limits_to_psi(
        self,
        band: Optional[Dict[str, Optional[float]]],
        min_psi: float,
        max_psi: float,
    ) -> Optional[Tuple[float, float]]:
        """Convert band limits to PSI."""
        if not band or not self._test_setup:
            return None
        units_label = self._test_setup.units_label or 'PSI'
        lower = band.get('lower')
        upper = band.get('upper')
        if lower is None or upper is None:
            return None
        lower_psi = convert_pressure(lower, units_label, 'PSI')
        upper_psi = convert_pressure(upper, units_label, 'PSI')
        if not math.isfinite(lower_psi):
            lower_psi = min_psi
        if not math.isfinite(upper_psi):
            upper_psi = max_psi
        low, high = sorted((lower_psi, upper_psi))
        if not math.isfinite(low) or not math.isfinite(high):
            return None
        return (low, high)
    
    def run_precision_sweep(
        self,
        sweep_mode: str,
        bounds: Tuple[float, float],
    ) -> Optional[SweepResult]:
        """Run precision sweep phase."""
        min_psi, max_psi = bounds
        atmosphere_psi = self._determine_atmosphere_psi()
        
        if min_psi >= max_psi:
            logger.error('Invalid sweep range %.3f to %.3f', min_psi, max_psi)
            return None
        
        activation_direction = self._resolve_activation_sweep_direction()
        
        # Atmosphere gate (only if not skipped by caller)
        hold_time = 0.1 if self._minimal_atmosphere_hold else self._precision_atmosphere_hold_s
        logger.info('Precision atmosphere gate start target=%.4f hold=%.2fs',
                   atmosphere_psi, hold_time)
        self._port.vent_to_atmosphere()
        if not self._wait_for_atmosphere(
            atmosphere_psi,
            timeout_s=self._edge_timeout_s,
            hold_s=hold_time,
        ):
            raise RuntimeError('Timeout waiting for precision atmosphere gate')
        
        # Resolve targets (using cycle estimates if available)
        approach_target, target_out, target_back, target_source = self._resolve_precision_targets(
            min_psi,
            max_psi,
            activation_direction,
        )
        
        logger.info('Precision targets: approach=%.4f out=%.4f back=%.4f source=%s',
                   approach_target, target_out, target_back, target_source)
        
        # Fast approach (optimized: reduce settle time if using cycle estimates)
        approach_target_abs = self._to_absolute(approach_target)
        self._set_pressure_or_raise(approach_target_abs)
        if not self._port.alicat.set_ramp_rate(self._fast_rate_psi):
            raise RuntimeError('Failed to set fast approach ramp rate')
        self._port.alicat.cancel_hold()
        
        if not self._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            raise RuntimeError(f'Failed to set solenoid route for {sweep_mode}')
        
        # Optimize settle time: use shorter settle if we have cycle estimates
        settle_time = self._precision_approach_settle_s
        if self._use_optimized_approach and (
            len(self._cycle_activation_samples) > 0 or len(self._cycle_deactivation_samples) > 0
        ):
            # Maximum reduction: settle time to absolute minimum (0.02s) when using cycle estimates
            # We're already very close from cycling, so minimal settle needed
            settle_time = 0.02
        
        if not self._wait_until_near_target(
            target_psi=approach_target,
            timeout_s=min(self._edge_timeout_s, 30.0),
            tolerance_psi=self._precision_approach_tolerance_psi,
            settle_s=settle_time,
        ):
            raise RuntimeError(f'Timeout waiting for precision approach target {approach_target:.3f} PSI')
        
        # Nudge away if already activated
        pressure, switch_state = self._read_pressure_and_switch_state()
        if switch_state is True:
            nudge_target = approach_target + (self._precision_prepass_nudge_psi * -activation_direction)
            nudge_target = min(max_psi, max(min_psi, nudge_target))
            if abs(nudge_target - approach_target) >= 0.02:
                logger.warning('Pre-pass switch already activated; nudging to %.4f PSI', nudge_target)
                self._set_pressure_or_raise(self._to_absolute(nudge_target))
                if not self._wait_until_near_target(
                    target_psi=nudge_target,
                    timeout_s=min(self._edge_timeout_s, 20.0),
                    tolerance_psi=self._precision_approach_tolerance_psi,
                    settle_s=self._precision_approach_settle_s,
                ):
                    raise RuntimeError('Timeout while nudging away from activated switch')
        
        # Precision sweep
        logger.info('Precision sweep direction=%s rate=%.4f psi/s',
                   'increasing' if activation_direction > 0 else 'decreasing',
                   self._slow_edge_rate_psi)
        result = self._execute_out_back_sweep(
            target_out,
            target_back,
            activation_direction,
            self._slow_edge_rate_psi,
        )
        
        return result
    
    def _run_precision_sweep_direct(
        self,
        sweep_mode: str,
        bounds: Tuple[float, float],
        approach_target: float,
        target_out: float,
        target_back: float,
    ) -> Optional[SweepResult]:
        """Run precision sweep with rapid jumps to minimize slow sweep distance."""
        min_psi, max_psi = bounds
        activation_direction = self._resolve_activation_sweep_direction()
        
        # Set solenoid route (if not already set)
        if not self._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            raise RuntimeError(f'Failed to set solenoid route for {sweep_mode}')
        
        # Get cycle estimates for rapid positioning
        activation_est = self._mean_or_none(self._cycle_activation_samples)
        deactivation_est = self._mean_or_none(self._cycle_deactivation_samples)
        
        if activation_est is None or deactivation_est is None:
            # Fallback to original approach if no estimates
            logger.warning('No cycle estimates - using full sweep approach')
            result = self._execute_out_back_sweep(
                target_out,
                target_back,
                activation_direction,
                self._slow_edge_rate_psi,
            )
            return result
        
        # OPTIMIZED APPROACH: Rapid jump to just below activation, slow sweep to activation,
        # then rapid jump to just above deactivation, slow sweep to deactivation
        # This minimizes the distance traveled at slow rate (5 torr/s)
        
        # Step 1: Rapid jump to just below activation
        # Use a conservative approach: start further below activation to avoid starting too close
        # Increased margin from 1.0 PSI to 2.0 PSI to provide more safety margin
        if len(self._cycle_activation_samples) > 0:
            min_activation = min(self._cycle_activation_samples)
            # Use 2.0 PSI below minimum to be very safe and avoid starting too close to activation
            rapid_start_below_activation = min_activation - 2.0
            # But don't go below 9.0 PSI if activation is around 9.6-10.6 PSI
            rapid_start_below_activation = max(9.0, rapid_start_below_activation)
        else:
            # Fallback: use estimate with larger margin
            rapid_start_below_activation = max(9.0, activation_est - 2.0)
        
        # Clamp to reasonable bounds
        rapid_start_below_activation = max(min_psi, rapid_start_below_activation)
        
        logger.info('Rapid jump to %.4f PSI (using min activation %.4f PSI, estimate %.4f PSI)',
                   rapid_start_below_activation,
                   min(self._cycle_activation_samples) if len(self._cycle_activation_samples) > 0 else activation_est,
                   activation_est)
        
        # Use moderate ramp rate (5 PSI/s) to avoid overshooting past activation
        moderate_rate_psi = 5.0
        if not self._port.alicat.set_ramp_rate(moderate_rate_psi):
            raise RuntimeError('Failed to set moderate ramp rate for jump to activation')
        self._set_pressure_or_raise(self._to_absolute(rapid_start_below_activation))
        self._port.alicat.cancel_hold()
        
        # Wait for rapid position, monitoring for early activation
        start_time = time.perf_counter()
        while time.perf_counter() - start_time < 10.0:
            reading = self._get_latest_reading(PORT_ID)
            if reading and reading.transducer:
                pressure_abs = self._reading_pressure_abs_psi(reading)
                if pressure_abs is not None:
                    pressure_test = self._absolute_to_test_reference(pressure_abs)
                    if abs(pressure_test - rapid_start_below_activation) <= 0.15:
                        # Near target - check switch state
                        switch_state = self._effective_switch_state(reading.switch) if reading.switch else None
                        if switch_state is True:
                            # Switch activated during jump - stop here
                            logger.info('Switch activated during rapid jump at %.4f PSI - stopping early', pressure_test)
                            rapid_start_below_activation = pressure_test - 0.1  # Back off slightly
                            break
                        # Close enough to target
                        time.sleep(0.05)  # Brief settle
                        break
            time.sleep(0.02)
        else:
            raise RuntimeError(f'Timeout waiting for rapid jump to {rapid_start_below_activation:.3f} PSI')
        
        # Check if switch is already activated - if so, nudge down first
        # Account for hysteresis - need to go down enough to ensure we're definitely below activation
        pressure, switch_state = self._read_pressure_and_switch_state()
        if switch_state is True:
            # Switch is already activated - nudge down well below activation (account for hysteresis ~1-2 PSI)
            # Use a larger nudge (1.0 PSI) to ensure we're definitely below activation
            nudge_down = rapid_start_below_activation - 1.0
            nudge_down = max(min_psi, nudge_down)
            logger.warning('Switch already activated at %.4f PSI - nudging down to %.4f PSI (accounting for hysteresis)',
                          rapid_start_below_activation, nudge_down)
            # Set fast rate for nudge
            if not self._port.alicat.set_ramp_rate(self._fast_rate_psi):
                raise RuntimeError('Failed to set fast ramp rate for nudge')
            self._set_pressure_or_raise(self._to_absolute(nudge_down))
            self._port.alicat.cancel_hold()
            if not self._wait_until_near_target(
                target_psi=nudge_down,
                timeout_s=10.0,
                tolerance_psi=0.1,
                settle_s=0.1,  # Slightly longer settle to ensure switch state stabilizes
            ):
                raise RuntimeError(f'Timeout while nudging down to {nudge_down:.3f} PSI')
            # Verify switch is now deactivated
            pressure_check, switch_state_check = self._read_pressure_and_switch_state()
            if switch_state_check is True:
                logger.warning('Switch still activated after nudge - may need larger nudge or wait longer')
            rapid_start_below_activation = nudge_down
        
        # Step 2: Slow sweep from just below activation until activation is detected
        logger.info('Slow sweep from %.4f PSI until activation detected (rate=%.4f PSI/s)',
                   rapid_start_below_activation, self._slow_edge_rate_psi)
        
        # Set slow ramp rate BEFORE setting target
        if not self._port.alicat.set_ramp_rate(self._slow_edge_rate_psi):
            raise RuntimeError('Failed to set slow ramp rate for precision sweep')
        self._port.alicat.cancel_hold()  # Cancel hold to start sweeping
        
        # Set target well past activation estimate (safety limit)
        # Use max activation from cycles + margin, or estimate + margin
        if len(self._cycle_activation_samples) > 0:
            max_activation = max(self._cycle_activation_samples)
            target_past_activation = max_activation + 0.5  # 0.5 PSI past max activation
        else:
            target_past_activation = activation_est + 0.5
        
        # Don't clamp to max_psi - allow going above if needed (activation might be above max_psi)
        # Only clamp if it's way beyond reasonable hardware limits
        hw_min, hw_max = self._resolve_hardware_limits_test_reference()
        if target_past_activation > hw_max:
            target_past_activation = hw_max
        
        edge_activation = self._sweep_to_edge(target_past_activation, activation_direction)
        if edge_activation is None:
            raise RuntimeError('Failed to detect activation edge during precision sweep')
        
        activation_psi = edge_activation.pressure_psi
        logger.info('Activation detected at %.4f PSI', activation_psi)
        
        # Step 3: Rapid jump to just above deactivation
        # Use the MAXIMUM deactivation from cycles (not average) to be safe
        # This ensures we're definitely above the actual deactivation point
        if len(self._cycle_deactivation_samples) > 0:
            max_deactivation = max(self._cycle_deactivation_samples)
            # Jump to 0.5 PSI above the maximum deactivation to be safe
            rapid_start_above_deactivation = max_deactivation + 0.5
        else:
            # Fallback: use estimate with larger margin
            rapid_start_above_deactivation = deactivation_est + 0.5
        
        # Clamp to reasonable bounds
        rapid_start_above_deactivation = min(max_psi + 0.5, rapid_start_above_deactivation)
        
        logger.info('Rapid jump to %.4f PSI (using max deactivation %.4f PSI, estimate %.4f PSI)',
                   rapid_start_above_deactivation,
                   max(self._cycle_deactivation_samples) if len(self._cycle_deactivation_samples) > 0 else deactivation_est,
                   deactivation_est)
        
        # Use a moderate ramp rate (not fastest) to avoid overshooting past deactivation
        # Use 5 PSI/s instead of 50 PSI/s to have better control
        moderate_rate_psi = 5.0  # Moderate rate to avoid overshooting
        if not self._port.alicat.set_ramp_rate(moderate_rate_psi):
            raise RuntimeError('Failed to set moderate ramp rate for jump to deactivation')
        self._set_pressure_or_raise(self._to_absolute(rapid_start_above_deactivation))
        self._port.alicat.cancel_hold()
        
        # Wait for rapid position, monitoring for early deactivation
        detected_deactivation_during_jump = None
        start_time = time.perf_counter()
        while time.perf_counter() - start_time < 10.0:
            reading = self._get_latest_reading(PORT_ID)
            if reading and reading.transducer:
                pressure_abs = self._reading_pressure_abs_psi(reading)
                if pressure_abs is not None:
                    pressure_test = self._absolute_to_test_reference(pressure_abs)
                    # Check switch state during jump
                    switch_state = self._effective_switch_state(reading.switch) if reading.switch else None
                    if switch_state is False and detected_deactivation_during_jump is None:
                        # Switch deactivated during jump - record this pressure
                        detected_deactivation_during_jump = pressure_test
                        logger.info('Switch deactivated during rapid jump at %.4f PSI', detected_deactivation_during_jump)
                    
                    if abs(pressure_test - rapid_start_above_deactivation) <= 0.15:
                        # Close enough to target
                        time.sleep(0.05)  # Brief settle
                        break
            time.sleep(0.02)
        else:
            raise RuntimeError(f'Timeout waiting for rapid jump to {rapid_start_above_deactivation:.3f} PSI')
        
        # If we detected deactivation during jump, use that to set a safe position above it
        if detected_deactivation_during_jump is not None:
            # Jump to 1.0 PSI above the detected deactivation point
            rapid_start_above_deactivation = detected_deactivation_during_jump + 1.0
            rapid_start_above_deactivation = min(max_psi + 0.5, rapid_start_above_deactivation)
            logger.info('Adjusting rapid start to %.4f PSI (1.0 PSI above detected deactivation %.4f PSI)',
                       rapid_start_above_deactivation, detected_deactivation_during_jump)
            # Set new target with moderate rate
            if not self._port.alicat.set_ramp_rate(moderate_rate_psi):
                raise RuntimeError('Failed to set moderate ramp rate for adjustment')
            self._set_pressure_or_raise(self._to_absolute(rapid_start_above_deactivation))
            self._port.alicat.cancel_hold()
            # Wait for adjustment
            if not self._wait_until_near_target(
                target_psi=rapid_start_above_deactivation,
                timeout_s=10.0,
                tolerance_psi=0.1,
                settle_s=0.1,
            ):
                raise RuntimeError(f'Timeout adjusting to {rapid_start_above_deactivation:.3f} PSI')
        
        # Final check - verify switch is activated before slow sweep
        # We need to be above the activation point to ensure switch is activated
        pressure, switch_state = self._read_pressure_and_switch_state()
        if switch_state is False:
            # Still deactivated - need to go above activation point
            # Use the detected activation pressure + small margin
            target_above_activation = activation_psi + 0.2  # 0.2 PSI above activation
            target_above_activation = min(max_psi + 0.5, target_above_activation)
            logger.warning('Switch still deactivated at %.4f PSI - going above activation to %.4f PSI (activation was %.4f PSI)',
                          rapid_start_above_deactivation, target_above_activation, activation_psi)
            if not self._port.alicat.set_ramp_rate(moderate_rate_psi):
                raise RuntimeError('Failed to set moderate ramp rate for final nudge')
            self._set_pressure_or_raise(self._to_absolute(target_above_activation))
            self._port.alicat.cancel_hold()
            if not self._wait_until_near_target(
                target_psi=target_above_activation,
                timeout_s=10.0,
                tolerance_psi=0.1,
                settle_s=0.15,  # Longer settle to ensure switch activates
            ):
                raise RuntimeError(f'Timeout while going above activation to {target_above_activation:.3f} PSI')
            # Verify switch is now activated
            pressure_check, switch_state_check = self._read_pressure_and_switch_state()
            if switch_state_check is False:
                raise RuntimeError(f'Switch still not activated at {target_above_activation:.4f} PSI (above activation {activation_psi:.4f} PSI)')
            rapid_start_above_deactivation = target_above_activation
        
        # Step 4: Slow sweep from just above deactivation until deactivation is detected
        logger.info('Slow sweep from %.4f PSI until deactivation detected (rate=%.4f PSI/s)',
                   rapid_start_above_deactivation, self._slow_edge_rate_psi)
        
        # Set slow ramp rate BEFORE setting target
        if not self._port.alicat.set_ramp_rate(self._slow_edge_rate_psi):
            raise RuntimeError('Failed to set slow ramp rate for precision sweep')
        self._port.alicat.cancel_hold()  # Cancel hold to start sweeping
        
        # Set target well below deactivation estimate (safety limit)
        # Use min deactivation from cycles - margin, or estimate - margin
        if len(self._cycle_deactivation_samples) > 0:
            min_deactivation = min(self._cycle_deactivation_samples)
            target_below_deactivation = min_deactivation - 0.5  # 0.5 PSI below min deactivation
        else:
            target_below_deactivation = deactivation_est - 0.5
        
        # Only clamp if it's way below reasonable hardware limits
        hw_min, hw_max = self._resolve_hardware_limits_test_reference()
        if target_below_deactivation < hw_min:
            target_below_deactivation = hw_min
        
        edge_deactivation = self._sweep_to_edge(target_below_deactivation, -activation_direction)
        if edge_deactivation is None:
            raise RuntimeError('Failed to detect deactivation edge during precision sweep')
        
        deactivation_psi = edge_deactivation.pressure_psi
        logger.info('Deactivation detected at %.4f PSI', deactivation_psi)
        
        # Return results
        return SweepResult(
            activation_psi=activation_psi,
            deactivation_psi=deactivation_psi,
        )


def get_latest_reading(port: Port) -> Optional[PortReading]:
    """Get latest reading from port."""
    return port.read_all()


def get_barometric_pressure(port: Port) -> float:
    """Get barometric pressure from Alicat."""
    reading = port.alicat.read_status()
    if reading and reading.barometric_pressure is not None:
        return reading.barometric_pressure
    return 14.7  # Default fallback


def save_results_csv(results: TestResults, output_dir: Path) -> Path:
    """Save test results to CSV file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'cycle_precision_{results.port_id}_{results.part_id}_{results.sequence_id}_{timestamp_str}.csv'
    filepath = output_dir / filename
    
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        
        # Header
        writer.writerow(['Cycle and Precision Test Results'])
        writer.writerow(['Part ID', results.part_id])
        writer.writerow(['Sequence ID', results.sequence_id])
        writer.writerow(['Port ID', results.port_id])
        writer.writerow(['Timestamp', results.timestamp])
        writer.writerow(['Total Duration (s)', f'{results.total_duration_s:.2f}'])
        writer.writerow(['Success', 'Yes' if results.success else 'No'])
        if results.error_message:
            writer.writerow(['Error', results.error_message])
        writer.writerow([])
        
        # Cycle results
        writer.writerow(['Cycling Phase'])
        writer.writerow(['Cycle', 'Activation Estimate (PSI)', 'Deactivation Estimate (PSI)', 'Duration (s)'])
        for cycle in results.cycles:
            writer.writerow([
                cycle.cycle_num,
                f'{cycle.activation_estimate:.4f}' if cycle.activation_estimate else 'N/A',
                f'{cycle.deactivation_estimate:.4f}' if cycle.deactivation_estimate else 'N/A',
                f'{cycle.duration_s:.2f}',
            ])
        writer.writerow([])
        
        # Precision results
        writer.writerow(['Precision Phase'])
        writer.writerow(['Activation (PSI)', f'{results.precision_activation_psi:.4f}' if results.precision_activation_psi else 'N/A'])
        writer.writerow(['Deactivation (PSI)', f'{results.precision_deactivation_psi:.4f}' if results.precision_deactivation_psi else 'N/A'])
        writer.writerow(['Precision Duration (s)', f'{results.total_duration_s - sum(c.duration_s for c in results.cycles):.2f}'])
    
    return filepath


def main() -> int:
    """Main test execution."""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    
    print("=" * 70)
    print("Cycle and Precision Test - Port A (15018 seq 399)")
    print("=" * 70)
    print()
    
    start_time = time.perf_counter()
    results = TestResults(
        part_id=PART_ID,
        sequence_id=SEQUENCE_ID,
        port_id=PORT_ID,
        timestamp=datetime.now().isoformat(),
        cycles=[],
        precision_activation_psi=None,
        precision_deactivation_psi=None,
        total_duration_s=0.0,
        success=False,
    )
    
    port = None
    try:
        # Load config
        print("Loading configuration...")
        config = load_config()
        setup_logging(config)
        
        # Initialize database
        print("Initializing database connection...")
        db_config = config.get('database', {})
        if not initialize_database(db_config):
            logger.warning("Database connection failed - attempting to continue")
        
        # Load PTP
        print(f"Loading PTP for {PART_ID}/{SEQUENCE_ID}...")
        ptp_params = load_ptp_from_db(PART_ID, SEQUENCE_ID)
        if not ptp_params:
            raise RuntimeError(f"No PTP parameters found for {PART_ID}/{SEQUENCE_ID}")
        
        is_valid, errors = validate_ptp_params(ptp_params)
        if not is_valid:
            raise RuntimeError(f"PTP validation failed: {', '.join(errors)}")
        
        test_setup = derive_test_setup(PART_ID, SEQUENCE_ID, ptp_params)
        print(f"PTP loaded: units={test_setup.units_label}, direction={test_setup.activation_direction}")
        
        # Initialize hardware
        print("Initializing Port A hardware...")
        labjack_config = config.get('hardware', {}).get('labjack', {})
        alicat_config = config.get('hardware', {}).get('alicat', {})
        solenoid_config = config.get('hardware', {}).get('solenoid', {})
        
        port_a_config = labjack_config.get(PORT_ID, {})
        alicat_a_config = alicat_config.get(PORT_ID, {})
        
        port = Port(
            PortId.PORT_A,
            {**labjack_config, **port_a_config},
            {**alicat_config, **alicat_a_config},
            solenoid_config,
        )
        
        # Configure from PTP
        if not port.configure_from_ptp(ptp_params):
            resolution = getattr(port, 'last_switch_resolution', None)
            details = '; '.join(getattr(resolution, 'errors', ()) or ())
            raise RuntimeError(f'PTP switch configuration failed for {PORT_ID}: {details}')
        
        # Connect hardware
        if not port.connect():
            raise RuntimeError("Failed to connect Port A hardware")
        print("Hardware connected successfully")
        
        # Create test executor
        def get_reading(_port_id: str) -> Optional[PortReading]:
            return get_latest_reading(port)
        
        def get_baro(_port_id: str) -> float:
            return get_barometric_pressure(port)
        
        executor = SimplifiedTestExecutor(
            port=port,
            test_setup=test_setup,
            config=config,
            get_latest_reading=get_reading,
            get_barometric_psi=get_baro,
        )
        
        # Determine sweep parameters
        sweep_mode = executor._resolve_sweep_mode()
        bounds = executor._resolve_sweep_bounds()
        atmosphere_psi = executor._determine_atmosphere_psi()
        
        print(f"\nTest Configuration:")
        print(f"  Sweep mode: {sweep_mode}")
        print(f"  Bounds: {bounds[0]:.4f} to {bounds[1]:.4f} PSI")
        print(f"  Atmosphere: {atmosphere_psi:.4f} PSI")
        print(f"  Precision rate: {executor._slow_edge_rate_torr:.1f} torr/s ({executor._slow_edge_rate_psi:.4f} PSI/s)")
        print()
        
        # Start from atmosphere
        print("Starting from atmosphere...")
        
        # Get initial reading to check current state
        initial_reading = get_latest_reading(port)
        if initial_reading and initial_reading.transducer:
            initial_pressure_abs = initial_reading.transducer.pressure
            initial_pressure_test = executor._absolute_to_test_reference(initial_pressure_abs)
            print(f"  Current pressure: {initial_pressure_abs:.4f} PSI (abs), {initial_pressure_test:.4f} PSI (test ref)")
        
        # Vent to atmosphere and set Alicat to atmosphere pressure explicitly
        port.vent_to_atmosphere()
        # Also set Alicat setpoint to atmosphere to ensure it's not holding at vacuum
        if atmosphere_psi == 0.0:
            # For gauge reference, set to barometric pressure (absolute)
            baro_psi = executor._get_barometric_psi(PORT_ID)
            port.set_pressure(baro_psi)
            print(f"  Set Alicat to {baro_psi:.4f} PSI (atmosphere absolute)")
        else:
            port.set_pressure(executor._to_absolute(atmosphere_psi))
            print(f"  Set Alicat to {executor._to_absolute(atmosphere_psi):.4f} PSI (atmosphere)")
        
        print("  Waiting for pressure to stabilize...", end=' ', flush=True)
        
        # Wait for atmosphere with progress reporting
        if not executor._wait_for_atmosphere(atmosphere_psi, timeout_s=90.0, hold_s=0.5):
            # Get final reading for debugging
            final_reading = get_latest_reading(port)
            if final_reading and final_reading.transducer:
                final_pressure = final_reading.transducer.pressure
                final_test = executor._absolute_to_test_reference(final_pressure)
                raise RuntimeError(
                    f"Failed to reach atmosphere. Final pressure: {final_pressure:.4f} PSI (abs), "
                    f"{final_test:.4f} PSI (test ref), target: {atmosphere_psi:.4f} PSI"
                )
            raise RuntimeError("Failed to reach atmosphere - no pressure reading available")
        print("OK")
        print()
        
        # Cycling phase
        print(f"Cycling phase ({executor._num_cycles} cycles)...")
        for cycle_num in range(1, executor._num_cycles + 1):
            cycle_start = time.perf_counter()
            print(f"  Cycle {cycle_num}/{executor._num_cycles}...", end=' ', flush=True)
            
            try:
                executor.run_single_cycle(sweep_mode, bounds)
                cycle_duration = time.perf_counter() - cycle_start
                
                # Get cycle estimates
                activation_est = executor._mean_or_none(executor._cycle_activation_samples)
                deactivation_est = executor._mean_or_none(executor._cycle_deactivation_samples)
                
                cycle_result = CycleResult(
                    cycle_num=cycle_num,
                    activation_estimate=activation_est,
                    deactivation_estimate=deactivation_est,
                    duration_s=cycle_duration,
                )
                results.cycles.append(cycle_result)
                
                print(f"Complete ({cycle_duration:.1f}s)")
                if activation_est:
                    print(f"    Activation estimate: {activation_est:.4f} PSI")
                if deactivation_est:
                    print(f"    Deactivation estimate: {deactivation_est:.4f} PSI")
            except Exception as e:
                print(f"FAILED: {e}")
                raise
        
        # After last cycle, we're at deactivation pressure
        # Skip atmosphere entirely - go directly to precision test with rapid jumps
        print("\nTransitioning directly to precision test (rapid jumps to minimize slow sweep)...")
        
        # Enable optimizations
        executor._use_optimized_approach = True
        executor._minimal_atmosphere_hold = True
        
        # Get cycle estimates
        activation_est = executor._mean_or_none(executor._cycle_activation_samples)
        deactivation_est = executor._mean_or_none(executor._cycle_deactivation_samples)
        if activation_est and deactivation_est:
            print(f"  Using cycle estimates: activation={activation_est:.4f} PSI, deactivation={deactivation_est:.4f} PSI")
        else:
            logger.warning("No cycle estimates available - precision test will use full sweep")
            # Fallback: resolve targets the old way
            activation_direction = executor._resolve_activation_sweep_direction()
            approach_target, target_out, target_back, target_source = executor._resolve_precision_targets(
                bounds[0],
                bounds[1],
                activation_direction,
            )
            precision_result = executor._run_precision_sweep_direct(
                sweep_mode,
                bounds,
                approach_target,
                target_out,
                target_back,
            )
            precision_duration = time.perf_counter() - precision_start
            if precision_result:
                results.precision_activation_psi = precision_result.activation_psi
                results.precision_deactivation_psi = precision_result.deactivation_psi
                results.success = True
                print(f"\nPrecision test complete ({precision_duration:.1f}s)")
                print(f"  Activation: {precision_result.activation_psi:.4f} PSI")
                print(f"  Deactivation: {precision_result.deactivation_psi:.4f} PSI")
            else:
                raise RuntimeError("Precision sweep did not detect edges")
            return 0
        
        # Precision test - optimized with rapid jumps
        print("=" * 70)
        print("Precision Test (rapid jumps + minimal slow sweep)...")
        print("=" * 70)
        
        precision_start = time.perf_counter()
        try:
            # Run precision sweep with rapid jumps (dummy targets, not used in optimized path)
            precision_result = executor._run_precision_sweep_direct(
                sweep_mode,
                bounds,
                0.0,  # Not used
                0.0,  # Not used
                0.0,  # Not used
            )
            precision_duration = time.perf_counter() - precision_start
            
            if precision_result:
                results.precision_activation_psi = precision_result.activation_psi
                results.precision_deactivation_psi = precision_result.deactivation_psi
                results.success = True
                
                print(f"\nPrecision test complete ({precision_duration:.1f}s)")
                print(f"  Activation: {precision_result.activation_psi:.4f} PSI")
                print(f"  Deactivation: {precision_result.deactivation_psi:.4f} PSI")
            else:
                raise RuntimeError("Precision sweep did not detect edges")
        except Exception as e:
            print(f"Precision test FAILED: {e}")
            results.error_message = str(e)
            raise
        
        # Return to atmosphere
        print("\nReturning to atmosphere...")
        port.vent_to_atmosphere()
        executor._wait_for_atmosphere(atmosphere_psi, timeout_s=60.0, hold_s=0.1)
        print("Atmosphere reached")
        
    except Exception as e:
        logger.error("Test failed: %s", e, exc_info=True)
        results.error_message = str(e)
        print(f"\nERROR: {e}")
        return 1
    
    finally:
        # Cleanup
        if port is not None:
            try:
                port.disconnect()
            except Exception:
                pass
        
        results.total_duration_s = time.perf_counter() - start_time
        
        # Save results
        try:
            output_dir = PROJECT_ROOT / 'scripts' / 'data'
            csv_path = save_results_csv(results, output_dir)
            print(f"\nResults saved to: {csv_path}")
        except Exception as e:
            logger.warning("Failed to save CSV: %s", e)
    
    print("\n" + "=" * 70)
    print("Test Complete")
    print("=" * 70)
    print(f"Total duration: {results.total_duration_s:.1f} seconds")
    print(f"Success: {'Yes' if results.success else 'No'}")
    
    return 0 if results.success else 1


if __name__ == '__main__':
    sys.exit(main())
