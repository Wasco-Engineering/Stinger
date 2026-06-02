"""
Test execution engine for cycling and precision sweep.

Runs in a background thread, driven by the state machine. Reports results
back to the controller via callbacks.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Dict, Optional

from app.hardware.port import Port, PortReading
from app.services.control_config import parse_control_config
from app.services.measurement_source import (
    get_measurement_settings,
    select_main_pressure_abs_psi,
)
from app.services.pressure_domain import to_absolute_pressure
from app.services.ptp_service import TestSetup, convert_pressure
from app.services.sweep_primitives import (
    DebounceState,
    EdgeDetection,
    SweepPassOutcome,
    SweepResult,
    observe_debounced_transition,
    resolve_sweep_result,
)
from app.services.sweep_utils import resolve_sweep_bounds, resolve_sweep_mode
from app.services.test_protocol import TestEvent, TestFailure, TestFailureCode

logger = logging.getLogger(__name__)


class CyclePhaseRunner:
    """Execute the cycling phase using executor context methods."""

    def __init__(self, ctx: 'TestExecutor') -> None:
        self._ctx = ctx

    def run_pre_approach(self, sweep_mode: str, bounds: tuple[float, float]) -> None:
        """Ramp quickly to just outside the test range before the first cycle.

        This eliminates the slow first-cycle problem where the system had
        to traverse all the way from atmosphere to the test range during the
        first cycling ramp.
        """
        if self._ctx._cancel_event.is_set():
            return
        min_psi, max_psi = bounds
        activation_direction = self._ctx._resolve_activation_sweep_direction()
        # Position on the deactivation side of the range so the first cycle
        # can immediately ramp toward activation:
        #   Increasing activation → deactivation is at the lower end
        #   Decreasing activation → deactivation is at the upper end
        if activation_direction > 0:
            pre_approach_target = min_psi
        else:
            pre_approach_target = max_psi

        pre_approach_abs = self._ctx._to_absolute(pre_approach_target)
        pre_approach_rate = self._ctx._fast_rate_psi * 3.0  # Extra-fast for pre-approach

        logger.info(
            '%s: Pre-approach ramping to %.4f PSI (abs=%.4f) at rate %.4f PSI/s',
            self._ctx._port_id,
            pre_approach_target,
            pre_approach_abs,
            pre_approach_rate,
        )

        if not self._ctx._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            self._ctx._fail(
                TestFailureCode.ROUTE_FAILURE,
                f'Failed to set solenoid route for pre-approach on {self._ctx._port_id} ({sweep_mode})',
            )

        # Set target and rate BEFORE canceling hold
        self._ctx._set_pressure_or_raise(pre_approach_abs)
        if not self._ctx._port.alicat.set_ramp_rate(pre_approach_rate):
            self._ctx._fail(
                TestFailureCode.RAMP_RATE_FAILURE,
                f'Failed to set pre-approach ramp rate for {self._ctx._port_id}',
            )
        self._ctx._port.alicat.cancel_hold()

        # Wait until close to the pre-approach target (generous tolerance)
        tolerance = max(0.5, (max_psi - min_psi) * 0.10)
        if not self._ctx._wait_until_near_target(
            target_psi=pre_approach_target,
            timeout_s=self._ctx._edge_timeout_s,
            tolerance_psi=tolerance,
            settle_s=0.1,
        ):
            logger.warning(
                '%s: Pre-approach did not reach target %.4f PSI within timeout; proceeding with cycling',
                self._ctx._port_id,
                pre_approach_target,
            )

    def run_single_cycle(self, sweep_mode: str, bounds: tuple[float, float]) -> None:
        """Run a single cycle: ramp fast until activation detected, then reverse until deactivation detected."""
        min_psi, max_psi = bounds
        direction = self._ctx._resolve_activation_sweep_direction()
        hw_min_psi, hw_max_psi = self._ctx._resolve_hardware_limits_test_reference()

        range_span = max_psi - min_psi
        overshoot = range_span * (self._ctx._overshoot_pct / 100.0)
        overshoot = max(overshoot, 0.5)

        # Safety targets: past the activation/deactivation sides of the range
        # These are used as fallback limits if edge detection fails
        if direction > 0:
            target_activation = max_psi + overshoot
            target_deactivation = max(0.0, min_psi - overshoot)
        else:
            target_activation = max(0.0, min_psi - overshoot)
            target_deactivation = max_psi + overshoot

        # Clamp targets to hardware limits
        target_activation = min(hw_max_psi, max(hw_min_psi, target_activation))
        target_deactivation = min(hw_max_psi, max(hw_min_psi, target_deactivation))

        if not self._ctx._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            self._ctx._fail(
                TestFailureCode.ROUTE_FAILURE,
                f'Failed to set solenoid route for {self._ctx._port_id} ({sweep_mode})',
            )

        # Set fast ramp rate
        if not self._ctx._port.alicat.set_ramp_rate(self._ctx._fast_rate_psi):
            self._ctx._fail(
                TestFailureCode.RAMP_RATE_FAILURE,
                f'Failed to set fast ramp rate for {self._ctx._port_id}',
            )

        # ---- Phase 1: Ramp toward activation until activation edge detected ----
        target_activation_abs = self._ctx._to_absolute(target_activation)
        self._ctx._set_pressure_or_raise(target_activation_abs)
        self._ctx._port.alicat.cancel_hold()

        logger.info(
            '%s: Cycle ramp toward activation (target=%.4f PSI)',
            self._ctx._port_id,
            target_activation,
        )

        # Wait for activation edge - check if debounce system commits an activation
        activation_samples_before = len(self._ctx._cycle_activation_samples)
        activation_detected, activation_diagnostic = self._ctx._wait_for_cycle_edge(
            target_psi=target_activation,
            direction=direction,
            edge_type='activation',
            samples_before=activation_samples_before,
            timeout_s=self._ctx._edge_timeout_s,
        )

        if self._ctx._cancel_event.is_set():
            return

        if not activation_detected:
            if activation_diagnostic == 'NO_SWITCH_DETECTED':
                logger.warning(
                    '%s: No switch detected - venting to atmosphere immediately',
                    self._ctx._port_id,
                )
                self._ctx._safe_vent()
                self._ctx._fail(
                    TestFailureCode.NO_SWITCH_DETECTED,
                    f'No switch detected on {self._ctx._port_id} - switch state did not change during pressure ramp',
                )
            else:
                error_msg = f'Activation edge not detected during cycle ramp on {self._ctx._port_id}'
                if activation_diagnostic:
                    error_msg += f': {activation_diagnostic}'
                self._ctx._fail(
                    TestFailureCode.EDGE_NOT_FOUND,
                    error_msg,
                )

        # ---- Phase 2: Immediately reverse direction and ramp toward deactivation until deactivation edge detected ----
        target_deactivation_abs = self._ctx._to_absolute(target_deactivation)
        self._ctx._set_pressure_or_raise(target_deactivation_abs)
        # Rate and solenoid are already set - just continue ramping
        self._ctx._port.alicat.cancel_hold()

        logger.info(
            '%s: Cycle ramp toward deactivation (target=%.4f PSI)',
            self._ctx._port_id,
            target_deactivation,
        )

        # Wait for deactivation edge - check if debounce system commits a deactivation
        deactivation_samples_before = len(self._ctx._cycle_deactivation_samples)
        deactivation_detected, deactivation_diagnostic = self._ctx._wait_for_cycle_edge(
            target_psi=target_deactivation,
            direction=-direction,
            edge_type='deactivation',
            samples_before=deactivation_samples_before,
            timeout_s=self._ctx._edge_timeout_s,
        )

        if self._ctx._cancel_event.is_set():
            return

        if not deactivation_detected:
            if deactivation_diagnostic == 'NO_SWITCH_DETECTED':
                logger.warning(
                    '%s: No switch detected - venting to atmosphere immediately',
                    self._ctx._port_id,
                )
                self._ctx._safe_vent()
                self._ctx._fail(
                    TestFailureCode.NO_SWITCH_DETECTED,
                    f'No switch detected on {self._ctx._port_id} - switch state did not change during pressure ramp',
                )
            else:
                error_msg = f'Deactivation edge not detected during cycle ramp on {self._ctx._port_id}'
                if deactivation_diagnostic:
                    error_msg += f': {deactivation_diagnostic}'
                self._ctx._fail(
                    TestFailureCode.EDGE_NOT_FOUND,
                    error_msg,
                )


class PrecisionPhaseRunner:
    """Execute the precision phase using executor context methods."""

    def __init__(self, ctx: 'TestExecutor') -> None:
        self._ctx = ctx

    def run_precision_sweep(
        self,
        sweep_mode: str,
        bounds: tuple[float, float],
        skip_atmosphere_gate: bool = False,
    ) -> Optional[SweepResult]:
        min_psi, max_psi = bounds
        atmosphere_psi = self._ctx._determine_atmosphere_psi()

        if min_psi >= max_psi:
            logger.error('%s: Invalid sweep range %.3f to %.3f', self._ctx._port_id, min_psi, max_psi)
            return None

        activation_direction = self._ctx._resolve_activation_sweep_direction()
        if not skip_atmosphere_gate:
            self._run_precision_atmosphere_gate(atmosphere_psi)
        else:
            logger.info(
                '%s: Skipping precision atmosphere gate - transitioning directly from cycling',
                self._ctx._port_id,
            )

        approach_target, target_out, target_back, target_source = self._ctx._resolve_precision_targets(
            min_psi,
            max_psi,
            activation_direction,
        )

        self._run_precision_fast_approach(sweep_mode, approach_target)

        adjusted_back = self._nudge_away_if_already_activated(
            activation_direction,
            approach_target,
            min_psi,
            max_psi,
        )
        if adjusted_back is not None:
            target_back = adjusted_back

        logger.info('%s: Precision final return target=%.4f PSI', self._ctx._port_id, target_back)
        self._log_precision_start_snapshot(target_source)

        logger.info(
            '%s: Precision sweep direction=%s source=%s approach=%.4f out=%.4f back=%.4f rate=%.4f psi/s',
            self._ctx._port_id,
            'increasing' if activation_direction > 0 else 'decreasing',
            target_source,
            approach_target,
            target_out,
            target_back,
            self._ctx._slow_edge_rate_psi,
        )
        outcome = self._ctx._run_sweep_pass(
            target_out,
            target_back,
            activation_direction,
            self._ctx._slow_edge_rate_psi,
        )
        result = outcome.result
        self._ctx._last_precision_missing_edge = outcome.missing_edge

        if self._ctx._cancel_event.is_set():
            return None

        if result:
            self._ctx._emit_substate('precision.exhaust')
            return result

        return None

    def _run_precision_atmosphere_gate(self, atmosphere_psi: float) -> None:
        self._ctx._emit_substate('precision.prep_atmosphere')
        logger.info(
            '%s: Precision atmosphere gate start target=%.4f hold=%.2fs',
            self._ctx._port_id,
            atmosphere_psi,
            self._ctx._precision_atmosphere_hold_s,
        )
        self._ctx._safe_vent()
        self._ctx._emit_substate('precision.hold_atmosphere')
        if self._ctx._wait_for_atmosphere(
            atmosphere_psi,
            timeout_s=self._ctx._edge_timeout_s,
            hold_s=self._ctx._precision_atmosphere_hold_s,
        ):
            return
        self._ctx._fail(
            TestFailureCode.ATMOSPHERE_TIMEOUT,
            f'Timeout waiting for precision atmosphere gate on {self._ctx._port_id}',
        )

    def _run_precision_fast_approach(self, sweep_mode: str, approach_target: float) -> None:
        self._ctx._emit_substate('precision.fast_approach')
        approach_target_abs = self._ctx._to_absolute(approach_target)
        # Set pressure target and rate BEFORE canceling hold to prevent
        # the Alicat from resuming toward the old (stale) cycling setpoint.
        self._ctx._set_pressure_or_raise(approach_target_abs)
        if not self._ctx._port.alicat.set_ramp_rate(self._ctx._fast_rate_psi):
            self._ctx._fail(
                TestFailureCode.RAMP_RATE_FAILURE,
                f'Failed to set fast approach ramp rate for {self._ctx._port_id}',
            )
        self._ctx._port.alicat.cancel_hold()
        logger.info(
            '%s: Precision handoff commanding approach target=%.4f PSI before solenoid switch',
            self._ctx._port_id,
            approach_target,
        )
        if not self._ctx._port.set_solenoid(to_vacuum=(sweep_mode == 'vacuum')):
            self._ctx._fail(
                TestFailureCode.ROUTE_FAILURE,
                f'Failed to set solenoid route for {self._ctx._port_id} ({sweep_mode})',
            )
        if self._ctx._wait_until_near_target(
            target_psi=approach_target,
            timeout_s=min(self._ctx._edge_timeout_s, 30.0),
            tolerance_psi=self._ctx._precision_approach_tolerance_psi,
            settle_s=self._ctx._precision_approach_settle_s,
        ):
            return
        self._ctx._fail(
            TestFailureCode.TARGET_TIMEOUT,
            f'Timeout waiting for precision approach target {approach_target:.3f} PSI on {self._ctx._port_id}',
        )

    def _log_precision_start_snapshot(self, target_source: str) -> None:
        start_pressure, start_switch = self._ctx._read_pressure_and_switch_state()
        logger.info(
            '%s: Precision start snapshot pressure=%s switch_activated=%s source=%s',
            self._ctx._port_id,
            f'{start_pressure:.4f} PSI' if start_pressure is not None else '--',
            start_switch,
            target_source,
        )

    def _nudge_away_if_already_activated(
        self,
        activation_direction: int,
        approach_target: float,
        min_psi: float,
        max_psi: float,
    ) -> Optional[float]:
        pressure, switch_state = self._ctx._read_pressure_and_switch_state()
        if switch_state is not True:
            return None

        nudge_target = approach_target + (self._ctx._precision_prepass_nudge_psi * -activation_direction)
        nudge_target = min(max_psi, max(min_psi, nudge_target))
        if abs(nudge_target - approach_target) < 0.02:
            logger.warning(
                '%s: Pre-pass switch already activated at pressure=%s; no room to nudge',
                self._ctx._port_id,
                f'{pressure:.4f} PSI' if pressure is not None else '--',
            )
            return None

        logger.warning(
            '%s: Pre-pass switch already activated at pressure=%s; nudging to %.4f PSI',
            self._ctx._port_id,
            f'{pressure:.4f} PSI' if pressure is not None else '--',
            nudge_target,
        )
        self._ctx._set_pressure_or_raise(self._ctx._to_absolute(nudge_target))
        if self._ctx._wait_until_near_target(
            target_psi=nudge_target,
            timeout_s=min(self._ctx._edge_timeout_s, 20.0),
            tolerance_psi=self._ctx._precision_approach_tolerance_psi,
            settle_s=self._ctx._precision_approach_settle_s,
        ):
            return nudge_target
        self._ctx._fail(
            TestFailureCode.TARGET_TIMEOUT,
            f'Timeout while nudging away from activated switch on {self._ctx._port_id}',
        )
        return None

class TestExecutor:
    """
    Executes the cycling + precision test sequence in a background thread.

    Usage:
        executor = TestExecutor(...)
        executor.start()       # launches background thread
        executor.request_cancel()  # to abort

    Callbacks are invoked from the background thread; the controller must
    marshal them to the main thread (e.g. via QTimer.singleShot).
    """

    def __init__(
        self,
        port_id: str,
        port: Port,
        test_setup: TestSetup,
        config: Dict[str, Any],
        get_latest_reading: Callable[[str], Optional[PortReading]],
        get_barometric_psi: Callable[[str], float],
        # Callbacks
        on_cycling_complete: Optional[Callable[[], None]] = None,
        on_substate_update: Optional[Callable[[str], None]] = None,
        on_edges_captured: Optional[Callable[[float, float], None]] = None,
        on_edge_detected: Optional[Callable[[str, float], None]] = None,  # edge_type, pressure_psi
        on_cycle_estimate: Optional[Callable[[Optional[float], Optional[float], int], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_cancelled: Optional[Callable[[], None]] = None,
        on_event: Optional[Callable[[TestEvent], None]] = None,
    ):
        self._port_id = port_id
        self._port = port
        self._test_setup = test_setup
        self._config = config
        self._get_latest_reading = get_latest_reading
        self._get_barometric_psi = get_barometric_psi

        # Callbacks
        self._on_cycling_complete = on_cycling_complete
        self._on_substate_update = on_substate_update
        self._on_edges_captured = on_edges_captured
        self._on_edge_detected = on_edge_detected
        self._on_cycle_estimate = on_cycle_estimate
        self._on_error = on_error
        self._on_cancelled = on_cancelled
        self._on_event = on_event

        # Control
        self._cancel_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Config values
        control_cfg = parse_control_config(config)

        self._num_cycles = control_cfg.cycling.num_cycles
        slow_torr_per_sec = control_cfg.ramps.precision_sweep_rate_torr_per_sec
        precision_edge_torr_per_sec = control_cfg.ramps.precision_edge_rate_torr_per_sec
        self._slow_rate_psi = convert_pressure(slow_torr_per_sec, 'Torr', 'PSI')
        self._slow_edge_rate_psi = convert_pressure(precision_edge_torr_per_sec, 'Torr', 'PSI')
        self._medium_rate_psi = self._slow_rate_psi * 3.0
        # Cycling rate: as fast as the controller allows.  100 PSI/s
        # (~5200 Torr/s) exceeds the physical ramp limit of common Alicat
        # controllers, so the hardware naturally governs the actual speed.
        self._fast_rate_psi = 100.0
        self._overshoot_pct = control_cfg.edge_detection.overshoot_beyond_limit_percent
        self._edge_timeout_s = control_cfg.edge_detection.timeout_sec
        self._atmosphere_tolerance_psi = control_cfg.edge_detection.atmosphere_tolerance_psi
        approach_tolerance_torr = control_cfg.edge_detection.precision_approach_tolerance_torr
        self._precision_approach_tolerance_psi = max(
            0.02,
            convert_pressure(approach_tolerance_torr, 'Torr', 'PSI'),
        )
        self._precision_approach_settle_s = control_cfg.edge_detection.precision_approach_settle_sec
        self._precision_atmosphere_hold_s = control_cfg.edge_detection.precision_start_atmosphere_hold_sec
        close_limit_offset_torr = control_cfg.edge_detection.precision_close_limit_offset_torr
        self._precision_close_limit_offset_psi = max(
            0.05,
            convert_pressure(close_limit_offset_torr, 'Torr', 'PSI'),
        )
        prepass_nudge_torr = control_cfg.edge_detection.precision_prepass_nudge_torr
        self._precision_prepass_nudge_psi = max(
            0.02,
            convert_pressure(prepass_nudge_torr, 'Torr', 'PSI'),
        )
        deactivation_margin_torr = control_cfg.edge_detection.precision_deactivation_margin_torr
        self._precision_deactivation_margin_psi = max(
            0.02,
            convert_pressure(deactivation_margin_torr, 'Torr', 'PSI'),
        )
        return_overshoot_torr = control_cfg.edge_detection.precision_return_overshoot_torr
        self._precision_return_overshoot_psi = max(
            0.0,
            convert_pressure(return_overshoot_torr, 'Torr', 'PSI'),
        )
        self._precision_post_target_grace_s = max(
            0.0,
            control_cfg.edge_detection.precision_post_target_grace_sec,
        )
        self._stable_count = control_cfg.debounce.stable_sample_count
        self._min_edge_interval_s = control_cfg.debounce.min_edge_interval_ms / 1000.0
        self._cycle_activation_samples: list[float] = []
        self._cycle_deactivation_samples: list[float] = []
        self._cycle_debounce_state = DebounceState()
        self._run_atmosphere_psi: Optional[float] = None
        self._last_precision_missing_edge: Optional[str] = None
        self._cycle_phase_runner = CyclePhaseRunner(self)
        self._precision_phase_runner = PrecisionPhaseRunner(self)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the test sequence in a background thread."""
        self._cancel_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f'TestExecutor-{self._port_id}',
            daemon=True,
        )
        self._thread.start()

    def request_cancel(self) -> None:
        """Request cancellation of the running test."""
        self._cancel_event.set()

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_debug_sweep_pass(
        self,
        bounds: tuple[float, float],
        direction: int,
        rate_psi_per_sec: float,
    ) -> Optional[tuple[float, float]]:
        """Run one out/back sweep pass and return activation/deactivation edges.

        This is a lightweight public entry point for debug workflows that need the
        same edge-detection behavior as production precision sweeps.
        """
        min_psi, max_psi = bounds
        target_out = max_psi if direction > 0 else min_psi
        target_back = min_psi if direction > 0 else max_psi

        result = self._execute_out_back_sweep(
            target_out=target_out,
            target_back=target_back,
            direction=direction,
            rate_psi_per_sec=rate_psi_per_sec,
            fail_on_rate_error=False,
        )
        if result.result is None:
            return None
        return (result.result.activation_psi, result.result.deactivation_psi)

    # ------------------------------------------------------------------
    # Internal: main loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main test execution sequence (runs in background thread)."""
        try:
            self._emit_event('run_started')
            self._ensure_alicat_units()
            self._run_atmosphere_psi = None
            pressure_ref = (
                str(self._test_setup.pressure_reference).strip().lower()
                if self._test_setup and self._test_setup.pressure_reference
                else 'absolute'
            )
            if pressure_ref == 'absolute':
                self._run_atmosphere_psi = self._get_barometric_psi(self._port_id)
            else:
                self._run_atmosphere_psi = 0.0
            sweep_mode = self._resolve_sweep_mode()
            bounds = self._resolve_sweep_bounds()
            logger.info(
                '%s: Sweep config mode=%s ref=%s bounds=%.4f..%.4f psi atmosphere=%.4f psi',
                self._port_id,
                sweep_mode,
                pressure_ref,
                bounds[0],
                bounds[1],
                self._run_atmosphere_psi,
            )

            # ---- Pre-approach: fast ramp to near edge of test range ----
            self._cycle_phase_runner.run_pre_approach(sweep_mode, bounds)
            if self._cancel_and_emit():
                return

            # ---- Cycling phase ----
            for cycle in range(1, self._num_cycles + 1):
                if self._cancel_and_emit():
                    return

                logger.info('%s: Cycle %d/%d', self._port_id, cycle, self._num_cycles)
                self._emit_event('cycle_started', cycle_index=cycle, cycle_total=self._num_cycles)
                self._run_single_cycle(sweep_mode, bounds)

            if self._on_cycling_complete:
                self._on_cycling_complete()

            if self._cancel_and_emit():
                return

            # ---- Precision test phase ----
            # Always return to a known baseline before precision so the
            # approach starts from the correct side of the activation edge.
            self._emit_event('precision_started', sweep_mode=sweep_mode)
            self._last_precision_missing_edge = None
            result = self._run_precision_sweep(sweep_mode, bounds)

            if self._cancel_and_emit():
                return

            if result is None:
                if self._last_precision_missing_edge == 'first':
                    failure_message = 'Activation edge not detected during precision out-sweep'
                elif self._last_precision_missing_edge == 'second':
                    failure_message = 'Deactivation edge not detected during precision return-sweep'
                else:
                    failure_message = 'No edges detected during precision sweep'
                self._fail(
                    TestFailureCode.EDGE_NOT_FOUND,
                    failure_message,
                )
                return

            # Return to atmosphere
            self._safe_vent()

            if self._on_edges_captured:
                self._on_edges_captured(result.activation_psi, result.deactivation_psi)
            self._emit_event(
                'run_completed',
                activation_psi=result.activation_psi,
                deactivation_psi=result.deactivation_psi,
            )

        except TestFailure as failure:
            logger.error('Test failure for %s: %s', self._port_id, failure)
            self._abort_with_error(failure)
        except Exception as exc:
            logger.error('Test executor error for %s: %s', self._port_id, exc, exc_info=True)
            self._abort_with_error(
                TestFailure(TestFailureCode.INTERNAL_ERROR, str(exc)),
            )
        finally:
            self._run_atmosphere_psi = None

    # ------------------------------------------------------------------
    # Cycling
    # ------------------------------------------------------------------

    def _run_single_cycle(self, sweep_mode: str, bounds: tuple[float, float]) -> None:
        self._cycle_phase_runner.run_single_cycle(sweep_mode, bounds)

    def _wait_for_target(
        self,
        target_psi: float,
        direction: int,
        timeout_s: float,
        collect_cycle_edges: bool = False,
    ) -> bool:
        """Wait until pressure reaches target (or timeout/cancel)."""
        start = time.perf_counter()
        target_abs = self._to_absolute(target_psi)

        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False

            reading = self._get_latest_reading(self._port_id)
            pressure_abs, _pressure_test = self._extract_pressures(
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

    def _wait_for_cycle_edge(
        self,
        target_psi: float,
        direction: int,
        edge_type: str,
        samples_before: int,
        timeout_s: float,
    ) -> tuple[bool, Optional[str]]:
        """Wait for a cycle edge (activation or deactivation) to be detected and committed by debounce system.
        
        Returns:
            Tuple of (success: bool, diagnostic_message: Optional[str])
            diagnostic_message contains failure details if success is False.
        """
        start = time.perf_counter()
        target_abs = self._to_absolute(target_psi)
        
        # Determine which samples list to monitor based on edge type
        if edge_type == 'activation':
            samples_list = self._cycle_activation_samples
        elif edge_type == 'deactivation':
            samples_list = self._cycle_deactivation_samples
        else:
            logger.error('%s: Unknown edge_type "%s"', self._port_id, edge_type)
            return False, f'Unknown edge_type "{edge_type}"'

        # Track diagnostic information for failure reporting
        last_pressure: Optional[float] = None
        last_switch_state: Optional[bool] = None
        initial_switch_state: Optional[bool] = None
        initial_switch_valid: Optional[bool] = None
        switch_state_changed = False
        target_reached = False
        target_reached_at: Optional[float] = None
        no_switch_samples = 0  # Count samples where switch appears disconnected

        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False, 'Test cancelled by user'

            reading = self._get_latest_reading(self._port_id)
            if reading is None:
                time.sleep(0.01)
                continue

            # Extract pressures with cycle edge collection enabled
            # This calls _observe_cycle_switch_sample which updates the debounce state
            pressure_abs, pressure_test = self._extract_pressures(
                reading,
                collect_cycle_edges=True,
            )
            
            # Track diagnostic info
            if pressure_test is not None:
                last_pressure = pressure_test
            
            # Check switch state for "no switch" detection
            switch_valid = True
            current_switch_state: Optional[bool] = None
            if reading.switch is not None:
                # Check if switch state is invalid (both NO and NC inactive = no switch connected)
                if not getattr(reading.switch, 'is_valid', True):
                    switch_valid = False
                    no_switch_samples += 1
                else:
                    current_switch_state = self._effective_switch_state(reading.switch)
                    if initial_switch_state is None:
                        initial_switch_state = current_switch_state
                        initial_switch_valid = switch_valid
                    elif current_switch_state != initial_switch_state:
                        switch_state_changed = True
                    last_switch_state = current_switch_state
            else:
                switch_valid = False
                no_switch_samples += 1

            # Early detection: if switch appears disconnected (invalid state) for significant time
            # or if we've reached target and switch never changed, likely no switch
            if pressure_abs is not None:
                reached_target = False
                if direction > 0 and pressure_abs >= target_abs:
                    reached_target = True
                elif direction < 0 and pressure_abs <= target_abs:
                    reached_target = True
                
                if reached_target and not target_reached:
                    target_reached = True
                    target_reached_at = time.perf_counter()
                    logger.warning(
                        '%s: Reached %s target %.4f PSI without detecting edge (continuing to wait)',
                        self._port_id,
                        edge_type,
                        target_psi,
                    )
                    
                    # If we reached target and switch never changed, likely no switch
                    if not switch_state_changed and initial_switch_state is not None:
                        elapsed_at_target = time.perf_counter() - start
                        wait_at_target = elapsed_at_target - (target_reached_at - start) if target_reached_at else 0
                        # Early detection: if we've waited 1 second at target with no switch change, detect as no switch
                        if wait_at_target > 1.0:
                            logger.warning(
                                '%s: No switch detected - reached target %.4f PSI but switch state unchanged after %.1fs - venting immediately',
                                self._port_id,
                                target_psi,
                                wait_at_target,
                            )
                            # Vent immediately and return failure
                            self._safe_vent()
                            return False, 'NO_SWITCH_DETECTED'
            
            # Check if debounce system committed an edge (samples list length increased)
            if len(samples_list) > samples_before:
                # Edge detected and committed - continue sampling briefly to ensure fully committed
                time.sleep(0.05)
                logger.debug(
                    '%s: %s edge detected and committed at %.4f PSI',
                    self._port_id,
                    edge_type,
                    samples_list[-1] if samples_list else 0.0,
                )
                return True, None

            # Safety check: if we've reached the target pressure without detecting edge, continue anyway
            # (This shouldn't happen in normal operation, but prevents infinite loops)
            if pressure_abs is not None:
                reached_target = False
                if direction > 0 and pressure_abs >= target_abs:
                    reached_target = True
                elif direction < 0 and pressure_abs <= target_abs:
                    reached_target = True
                
                if reached_target and not target_reached:
                    target_reached = True
                    target_reached_at = time.perf_counter()
                    logger.warning(
                        '%s: Reached %s target %.4f PSI without detecting edge (continuing to wait)',
                        self._port_id,
                        edge_type,
                        target_psi,
                    )

            time.sleep(0.01)

        # Check for "no switch detected" condition
        # Criteria: switch state never changed AND we reached target pressure
        # OR switch readings show invalid state (both NO/NC inactive) consistently
        elapsed = time.perf_counter() - start
        no_switch_detected = False
        
        if target_reached and not switch_state_changed:
            # Reached target but switch never changed - likely no switch
            wait_after_target = elapsed - (target_reached_at - start) if target_reached_at else 0
            # Detect earlier - wait only 1 second at target instead of 2
            if wait_after_target > 1.0:  # Waited at least 1 second at target
                no_switch_detected = True
                logger.warning(
                    '%s: No switch detected - reached target %.4f PSI but switch state never changed (waited %.1fs)',
                    self._port_id,
                    target_psi,
                    wait_after_target,
                )
        
        # Also check if switch readings show invalid state consistently
        # Lower threshold - detect if more than 30% of samples show invalid switch
        total_samples = int(elapsed * 100)  # Rough estimate: ~100 samples per second
        if total_samples > 0 and no_switch_samples > max(30, total_samples * 0.3):
            no_switch_detected = True
            logger.warning(
                '%s: No switch detected - switch readings show invalid state (both NO/NC inactive) in %d/%d samples',
                self._port_id,
                no_switch_samples,
                total_samples,
            )
        
        if no_switch_detected:
            # Return special indicator for no switch
            return False, 'NO_SWITCH_DETECTED'
        
        # Timeout occurred - build diagnostic message
        diagnostic_parts = [
            f'Timeout after {elapsed:.1f}s waiting for {edge_type} edge',
            f'Target pressure: {target_psi:.4f} PSI',
        ]
        
        if last_pressure is not None:
            diagnostic_parts.append(f'Final pressure reached: {last_pressure:.4f} PSI')
            pressure_error = abs(last_pressure - target_psi)
            if pressure_error > 0.1:
                diagnostic_parts.append(f'Pressure error: {pressure_error:.4f} PSI from target')
        
        if last_switch_state is not None:
            expected_state = edge_type == 'activation'
            diagnostic_parts.append(
                f'Switch state: {"activated" if last_switch_state else "deactivated"} '
                f'(expected: {"activated" if expected_state else "deactivated"})'
            )
            if last_switch_state == expected_state:
                diagnostic_parts.append('WARNING: Switch is already in expected state - may indicate wiring issue or switch stuck')
        elif initial_switch_valid is False:
            diagnostic_parts.append('Switch readings show invalid state - check wiring')
        
        if target_reached:
            wait_after_target = elapsed - (target_reached_at - start) if target_reached_at else 0
            diagnostic_parts.append(
                f'Target reached at {target_reached_at - start:.1f}s, waited additional {wait_after_target:.1f}s'
            )
        else:
            diagnostic_parts.append('Target pressure was not reached before timeout')
        
        diagnostic_msg = '; '.join(diagnostic_parts)
        logger.warning(
            '%s: Timeout waiting for %s edge (target=%.4f PSI) - %s',
            self._port_id,
            edge_type,
            target_psi,
            diagnostic_msg,
        )
        return False, diagnostic_msg

    def _wait_until_near_target(
        self,
        target_psi: float,
        timeout_s: float,
        tolerance_psi: float,
        settle_s: float,
    ) -> bool:
        """Wait until pressure stays within tolerance around target."""
        start = time.perf_counter()
        target_abs = self._to_absolute(target_psi)
        near_since: Optional[float] = None

        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False

            reading = self._get_latest_reading(self._port_id)
            if reading is None:
                time.sleep(0.02)
                continue

            pressure_abs = self._reading_pressure_abs_psi(reading)
            if pressure_abs is None:
                time.sleep(0.02)
                continue
            error = abs(pressure_abs - target_abs)
            if error <= tolerance_psi:
                now = time.perf_counter()
                if near_since is None:
                    near_since = now
                elif now - near_since >= max(0.0, settle_s):
                    return True
            else:
                near_since = None

            time.sleep(0.02)

        logger.warning(
            '%s: Timeout (%.1fs) waiting near approach target=%.4f tol=%.4f',
            self._port_id,
            timeout_s,
            target_psi,
            tolerance_psi,
        )
        return False

    def _read_pressure_and_switch_state(self) -> tuple[Optional[float], Optional[bool]]:
        reading = self._get_latest_reading(self._port_id)
        if reading is None:
            return None, None
        pressure = self._reading_pressure_test_psi(reading)
        if reading.switch is None:
            return pressure, None
        return pressure, self._effective_switch_state(reading.switch)

    # ------------------------------------------------------------------
    # Precision sweep
    # ------------------------------------------------------------------

    def _run_precision_sweep(
        self,
        sweep_mode: str,
        bounds: tuple[float, float],
        skip_atmosphere_gate: bool = False,
    ) -> Optional[SweepResult]:
        return self._precision_phase_runner.run_precision_sweep(sweep_mode, bounds, skip_atmosphere_gate)

    def _run_sweep_pass(
        self,
        target_out: float,
        target_back: float,
        direction: int,
        rate_psi_per_sec: float,
    ) -> SweepPassOutcome:
        """Single sweep pass: out to edge, then back to edge."""

        logger.info(
            '%s: Precision pass direction=%s out=%.4f back=%.4f psi',
            self._port_id,
            'increasing' if direction > 0 else 'decreasing',
            target_out,
            target_back,
        )

        return self._execute_out_back_sweep(
            target_out=target_out,
            target_back=target_back,
            direction=direction,
            rate_psi_per_sec=rate_psi_per_sec,
            fail_on_rate_error=True,
        )

    def _execute_out_back_sweep(
        self,
        target_out: float,
        target_back: float,
        direction: int,
        rate_psi_per_sec: float,
        fail_on_rate_error: bool,
    ) -> SweepPassOutcome:
        if not self._port.alicat.set_ramp_rate(rate_psi_per_sec):
            if fail_on_rate_error:
                self._fail(TestFailureCode.RAMP_RATE_FAILURE, f'Failed to set sweep ramp rate for {self._port_id}')
            return SweepPassOutcome(result=None, missing_edge='rate_error')

        # First edge is activation (sweeping in activation direction)
        edge_out = self._sweep_to_edge(target_out, direction, edge_type='activation')
        if self._cancel_event.is_set():
            return SweepPassOutcome(result=None, missing_edge='cancelled')
        if edge_out is None:
            logger.warning(
                '%s: No first edge detected in precision out-sweep target=%.3f',
                self._port_id,
                target_out,
            )
            return SweepPassOutcome(result=None, missing_edge='first')

        # Second edge is deactivation (sweeping in opposite direction)
        hw_min_psi, hw_max_psi = self._resolve_hardware_limits_test_reference()
        return_target = target_back + (self._precision_return_overshoot_psi * (-direction))
        return_target = min(hw_max_psi, max(hw_min_psi, return_target))
        if not math.isclose(return_target, target_back, abs_tol=1e-6):
            logger.debug(
                '%s: Precision return target expanded %.4f -> %.4f PSI (overshoot=%.4f)',
                self._port_id,
                target_back,
                return_target,
                self._precision_return_overshoot_psi,
            )
        edge_back = self._sweep_to_edge(return_target, -direction, edge_type='deactivation')
        if self._cancel_event.is_set():
            return SweepPassOutcome(result=None, missing_edge='cancelled')
        if edge_back is None:
            logger.warning(
                '%s: Second edge not detected in precision return-sweep target=%.3f',
                self._port_id,
                return_target,
            )
            return SweepPassOutcome(result=None, missing_edge='second')

        return SweepPassOutcome(
            result=resolve_sweep_result(edge_out, edge_back),
            missing_edge=None,
        )

    def _sweep_to_edge(
        self,
        target_psi: float,
        direction: int,
        edge_type: Optional[str] = None,
    ) -> Optional[EdgeDetection]:
        """Sweep toward target, returning the first stable edge detected.
        
        Args:
            target_psi: Target pressure to sweep toward
            direction: Sweep direction (>0 for increasing, <0 for decreasing)
            edge_type: Optional type label ('activation' or 'deactivation') for immediate callback
        """
        target_abs = self._to_absolute(target_psi)
        self._set_pressure_or_raise(target_abs)

        reading_start = self._get_latest_reading(self._port_id)
        dynamic_timeout_s = self._edge_timeout_s
        rate_psi_per_sec = self._current_rate_psi_per_sec()
        if reading_start is not None:
            start_abs = self._reading_pressure_abs_psi(reading_start)
            if start_abs is not None:
                travel_psi = abs(target_abs - start_abs)
                estimated_travel_s = travel_psi / max(1e-4, rate_psi_per_sec)
                dynamic_timeout_s = max(self._edge_timeout_s, estimated_travel_s * 1.35 + 8.0)

        # Pre-initialize debounce with the current switch state from
        # reading_start so that the sweep can detect an edge even when the
        # approach target is very close to the activation/deactivation point.
        # Without this, the first loop sample might already be on the "other
        # side" of the edge and the debounce would treat it as the starting
        # state — never detecting a transition.
        initial_switch_state: Optional[bool] = None
        if reading_start is not None and reading_start.switch is not None:
            initial_switch_state = self._effective_switch_state(reading_start.switch)
        debounce_state = DebounceState(last_state=initial_switch_state)

        start = time.perf_counter()
        target_reached_since: Optional[float] = None
        settle_window_s = max(0.08, self._stable_count * 0.02)
        target_grace_s = settle_window_s + self._precision_post_target_grace_s

        while time.perf_counter() - start < dynamic_timeout_s:
            if self._cancel_event.is_set():
                return None

            reading = self._get_latest_reading(self._port_id)
            if reading is None or reading.switch is None:
                time.sleep(0.01)
                continue

            pressure_abs = self._reading_pressure_abs_psi(reading)
            if pressure_abs is None:
                time.sleep(0.01)
                continue
            pressure = self._absolute_to_test_reference(pressure_abs)
            current_state = self._effective_switch_state(reading.switch)

            if debounce_state.last_state is not None and current_state != debounce_state.last_state:
                logger.debug(
                    '%s: Switch transition during sweep at %.4f PSI (to %s)',
                    self._port_id,
                    pressure,
                    current_state,
                )

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
                # Use the first-detection pressure for better accuracy
                edge_pressure = committed_pressure if committed_pressure is not None else pressure
                # Report edge immediately if callback is available
                if self._on_edge_detected and edge_type:
                    self._on_edge_detected(edge_type, edge_pressure)
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
                elif now - target_reached_since >= target_grace_s:
                    logger.debug(
                        '%s: Sweep target reached without stable edge target=%.4f dir=%s hold=%.3fs',
                        self._port_id,
                        target_psi,
                        'increasing' if direction > 0 else 'decreasing',
                        now - target_reached_since,
                    )
                    break
            else:
                target_reached_since = None

            time.sleep(0.01)

        return None

    def _current_rate_psi_per_sec(self) -> float:
        """Best-effort current commanded precision sweep rate in PSI/s."""
        return max(1e-4, self._slow_edge_rate_psi)

    def _emit_substate(self, substate: str) -> None:
        if self._on_substate_update:
            self._on_substate_update(substate)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _wait_for_atmosphere(
        self,
        atmosphere_psi: float,
        timeout_s: float,
        collect_cycle_edges: bool = False,
        hold_s: float = 0.0,
    ) -> bool:
        """Wait for port pressure to return near atmosphere after venting."""
        threshold_psi = max(0.05, self._atmosphere_tolerance_psi)
        start = time.perf_counter()
        near_since: Optional[float] = None

        while time.perf_counter() - start < timeout_s:
            if self._cancel_event.is_set():
                return False

            reading = self._get_latest_reading(self._port_id)
            pressure = self._reading_pressure_for_wait(reading, collect_cycle_edges)
            if pressure is None:
                time.sleep(0.05)
                continue

            if abs(pressure - atmosphere_psi) <= threshold_psi:
                now = time.perf_counter()
                if near_since is None:
                    near_since = now
                    if hold_s > 0.0:
                        logger.info(
                            '%s: Atmosphere reached pressure=%.4f target=%.4f tol=%.3f hold=%.2fs',
                            self._port_id,
                            pressure,
                            atmosphere_psi,
                            threshold_psi,
                            hold_s,
                        )
                if now - near_since >= max(0.0, hold_s):
                    if hold_s > 0.0:
                        logger.info('%s: Atmosphere hold complete (%.2fs)', self._port_id, hold_s)
                    return True
            else:
                near_since = None

            time.sleep(0.05)

        logger.warning(
            '%s: Timeout (%.1fs) waiting for atmosphere target=%.4f tol=%.3f',
            self._port_id,
            timeout_s,
            atmosphere_psi,
            threshold_psi,
        )
        return False

    def _reading_pressure_for_wait(
        self,
        reading: Optional[PortReading],
        collect_cycle_edges: bool,
    ) -> Optional[float]:
        _pressure_abs, pressure_test = self._extract_pressures(
            reading,
            collect_cycle_edges=collect_cycle_edges,
        )
        if pressure_test is None:
            return None
        return pressure_test

    def _extract_pressures(
        self,
        reading: Optional[PortReading],
        collect_cycle_edges: bool = False,
    ) -> tuple[Optional[float], Optional[float]]:
        if reading is None:
            return None, None
        pressure_abs = self._reading_pressure_abs_psi(reading)
        if pressure_abs is None:
            return None, None
        pressure_test = self._absolute_to_test_reference(pressure_abs)
        if collect_cycle_edges:
            self._observe_cycle_switch_sample(pressure_test, reading.switch)
        return pressure_abs, pressure_test

    def _resolve_activation_sweep_direction(self) -> int:
        """Resolve precision sweep direction from PTP activation direction."""
        direction = (self._test_setup.activation_direction or '').strip().lower() if self._test_setup else ''
        if direction.startswith('decreas') or direction in {'down', 'falling'}:
            return -1
        return 1

    def _resolve_precision_targets(
        self,
        min_psi: float,
        max_psi: float,
        activation_direction: int,
    ) -> tuple[float, float, float, str]:
        """Resolve precision approach/out/back targets.

        Close-limit semantics:
        - Fast approach to the nearest deactivation boundary from atmosphere.
        - Slow sweep from that close limit to the activation-side boundary.
        - Reverse back to the close limit to capture the return edge.
        """
        activation_estimate, deactivation_estimate = self._ordered_cycle_estimates()

        if activation_estimate is not None and deactivation_estimate is not None:
            offset = self._precision_close_limit_offset_psi
            margin = self._precision_deactivation_margin_psi
            # Get hardware limits as absolute bounds (allow going slightly outside PTP bounds if needed)
            hw_min, hw_max = self._resolve_hardware_limits_test_reference()

            # Resolve the PTP activation band so the sweep is guaranteed to
            # cover the full acceptance window even when the cycle estimate
            # is biased away from the real activation point.
            act_band = self._resolve_activation_band_psi(activation_direction, min_psi, max_psi)

            if activation_direction < 0:
                # Decreasing activation: activation at lower pressure,
                # deactivation at higher pressure.
                # Approach from above, just above the activation point.
                approach_target = min(max_psi, activation_estimate + offset)
                # Slow sweep down past activation to find the activation edge.
                target_out = max(hw_min, activation_estimate - offset)
                # Widen to cover the activation band if the estimate is off
                if act_band:
                    approach_target = max(approach_target, act_band[1] + offset * 0.25)
                    target_out = min(target_out, act_band[0] - offset * 0.25)
                    approach_target = min(hw_max, approach_target)
                    target_out = max(hw_min, target_out)
                # Reverse sweep up past deactivation to find the deactivation edge.
                target_back = min(hw_max, deactivation_estimate + margin)
            else:
                # Increasing activation: activation at higher pressure,
                # deactivation at lower pressure.
                # Approach from below, just below the activation point.
                approach_target = max(min_psi, activation_estimate - offset)
                # Slow sweep up past activation to find the activation edge.
                target_out = min(hw_max, activation_estimate + offset)
                # Widen to cover the activation band if the estimate is off
                if act_band:
                    approach_target = min(approach_target, act_band[0] - offset * 0.25)
                    target_out = max(target_out, act_band[1] + offset * 0.25)
                    approach_target = max(hw_min, approach_target)
                    target_out = min(hw_max, target_out)
                # Reverse sweep down past deactivation to find the deactivation edge.
                target_back = max(hw_min, deactivation_estimate - margin)
            validation_error = self._validate_cycle_estimate_targets(
                activation_direction=activation_direction,
                approach_target=approach_target,
                target_out=target_out,
                target_back=target_back,
                activation_estimate=activation_estimate,
                deactivation_estimate=deactivation_estimate,
            )
            if validation_error is None:
                oob_parts: list[str] = []
                if activation_direction < 0:
                    if target_out < min_psi:
                        oob_parts.append(f'target_out below PTP min={min_psi:.4f}')
                    if target_back > max_psi:
                        oob_parts.append(f'target_back above PTP max={max_psi:.4f}')
                else:
                    if target_out > max_psi:
                        oob_parts.append(f'target_out above PTP max={max_psi:.4f}')
                    if target_back < min_psi:
                        oob_parts.append(f'target_back below PTP min={min_psi:.4f}')
                out_of_bounds_note = f' ({"; ".join(oob_parts)})' if oob_parts else ''
                logger.info(
                    '%s: Precision targets from cycle estimates: approach=%.4f out=%.4f back=%.4f '
                    '(act_est=%.4f deact_est=%.4f offset=%.4f margin=%.4f)%s',
                    self._port_id,
                    approach_target,
                    target_out,
                    target_back,
                    activation_estimate,
                    deactivation_estimate,
                    offset,
                    margin,
                    out_of_bounds_note,
                )
                return (approach_target, target_out, target_back, 'cycle-estimate-offset-close-limit')
            logger.warning(
                '%s: Rejecting cycle-estimate precision targets: %s '
                '(direction=%s act_est=%.4f deact_est=%.4f approach=%.4f out=%.4f back=%.4f)',
                self._port_id,
                validation_error,
                'increasing' if activation_direction > 0 else 'decreasing',
                activation_estimate,
                deactivation_estimate,
                approach_target,
                target_out,
                target_back,
            )

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
                        logger.info(
                            '%s: Precision targets from PTP close-limit (decreasing): '
                            'approach=%.4f out=%.4f back=%.4f',
                            self._port_id,
                            close_limit,
                            act_low,
                            close_limit,
                        )
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
                        logger.info(
                            '%s: Precision targets from PTP close-limit (increasing): '
                            'approach=%.4f out=%.4f back=%.4f',
                            self._port_id,
                            close_limit,
                            act_high,
                            close_limit,
                        )
                        return (close_limit, act_high, close_limit, 'ptp-close-limit')

        if activation_direction < 0:
            logger.info(
                '%s: Precision targets from bounds close-limit (decreasing): '
                'approach=%.4f out=%.4f back=%.4f',
                self._port_id,
                max_psi,
                min_psi,
                max_psi,
            )
            return (max_psi, min_psi, max_psi, 'bounds-close-limit')
        logger.info(
            '%s: Precision targets from bounds close-limit (increasing): '
            'approach=%.4f out=%.4f back=%.4f',
            self._port_id,
            min_psi,
            max_psi,
            min_psi,
        )
        return (min_psi, max_psi, min_psi, 'bounds-close-limit')

    def _validate_cycle_estimate_targets(
        self,
        activation_direction: int,
        approach_target: float,
        target_out: float,
        target_back: float,
        activation_estimate: float,
        deactivation_estimate: float,
    ) -> Optional[str]:
        eps = 1e-6
        if activation_direction > 0:
            if activation_estimate <= deactivation_estimate + eps:
                return 'activation estimate is not above deactivation estimate for increasing direction'
            if not (approach_target < target_out):
                return 'approach/out ordering does not sweep upward for increasing direction'
            if not (approach_target <= activation_estimate <= target_out):
                return 'activation estimate is not bracketed by increasing out-sweep'
            if not (target_back <= deactivation_estimate <= target_out):
                return 'deactivation estimate is not bracketed by return sweep'
            return None

        if activation_estimate >= deactivation_estimate - eps:
            return 'activation estimate is not below deactivation estimate for decreasing direction'
        if not (approach_target > target_out):
            return 'approach/out ordering does not sweep downward for decreasing direction'
        if not (approach_target >= activation_estimate >= target_out):
            return 'activation estimate is not bracketed by decreasing out-sweep'
        if not (target_back >= deactivation_estimate >= target_out):
            return 'deactivation estimate is not bracketed by return sweep'
        return None

    def _band_limits_to_psi(
        self,
        band: Optional[Dict[str, Optional[float]]],
        min_psi: float,
        max_psi: float,
    ) -> Optional[tuple[float, float]]:
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

    def _resolve_activation_band_psi(
        self,
        activation_direction: int,
        min_psi: float,
        max_psi: float,
    ) -> Optional[tuple[float, float]]:
        """Return the PTP activation acceptance band in PSI, if available."""
        if not self._test_setup:
            return None
        band_key = 'decreasing' if activation_direction < 0 else 'increasing'
        return self._band_limits_to_psi(
            self._test_setup.bands.get(band_key), min_psi, max_psi,
        )

    def _resolve_sweep_mode(self) -> str:
        """Determine whether to sweep in pressure or vacuum direction."""
        return resolve_sweep_mode(
            self._test_setup,
            atmosphere_psi=self._determine_atmosphere_psi(),
        )

    def _resolve_sweep_bounds(self) -> tuple[float, float]:
        """Get sweep bounds from PTP or config fallback."""
        labjack_cfg = self._config.get('hardware', {}).get('labjack', {})
        port_cfg = labjack_cfg.get(self._port_id, {})
        return resolve_sweep_bounds(self._test_setup, port_cfg)

    def _determine_atmosphere_psi(self) -> float:
        pressure_ref = self._test_setup.pressure_reference if self._test_setup else None
        if str(pressure_ref or '').strip().lower() == 'absolute':
            if self._run_atmosphere_psi is not None:
                return self._run_atmosphere_psi
            return self._get_barometric_psi(self._port_id)
        return 0.0

    def _to_absolute(self, value_psi: float) -> float:
        """Convert a PSI value to absolute if PTP is gauge-referenced."""
        pressure_ref = self._test_setup.pressure_reference if self._test_setup else None
        return to_absolute_pressure(value_psi, pressure_ref, self._get_barometric_psi(self._port_id))

    @staticmethod
    def _effective_switch_state(switch_state: Any) -> bool:
        if switch_state is None:
            return False
        if getattr(switch_state, 'is_valid', True):
            return bool(switch_state.switch_activated)
        return bool(getattr(switch_state, 'no_active', False))

    def _observe_cycle_switch_sample(self, pressure_test_psi: float, switch_state: Any) -> None:
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

        if not getattr(switch_state, 'is_valid', True):
            logger.warning(
                '%s: Switch transition with invalid NO/NC state (NO=%s NC=%s)',
                self._port_id,
                getattr(switch_state, 'no_active', None),
                getattr(switch_state, 'nc_active', None),
            )

        # Use the first-detection pressure for better accuracy during fast ramps
        sample_pressure = committed_pressure if committed_pressure is not None else pressure_test_psi
        if activated:
            self._cycle_activation_samples.append(sample_pressure)
        else:
            self._cycle_deactivation_samples.append(sample_pressure)

        activation, deactivation = self._ordered_cycle_estimates()
        count = max(len(self._cycle_activation_samples), len(self._cycle_deactivation_samples))
        logger.debug(
            '%s: Cycle estimate act=%s deact=%s samples=%d',
            self._port_id,
            f'{activation:.4f}' if activation is not None else '--',
            f'{deactivation:.4f}' if deactivation is not None else '--',
            count,
        )
        if self._on_cycle_estimate:
            self._on_cycle_estimate(activation, deactivation, count)

    def _ordered_cycle_estimates(self) -> tuple[Optional[float], Optional[float]]:
        """Return cycle estimates ordered consistently with the activation direction.

        For increasing activation, activation should be above deactivation.
        For decreasing activation, activation should be below deactivation.
        If the raw switch-state labels produced the opposite ordering, swap
        them so downstream consumers (display and precision targets) see
        the correct semantics.
        """
        activation = self._mean_or_none(self._cycle_activation_samples)
        deactivation = self._mean_or_none(self._cycle_deactivation_samples)
        if activation is not None and deactivation is not None:
            direction = self._resolve_activation_sweep_direction()
            needs_swap = (
                (direction > 0 and activation < deactivation)
                or (direction < 0 and activation > deactivation)
            )
            if needs_swap:
                logger.debug(
                    '%s: Reordering cycle estimates for %s direction: '
                    'raw act=%.4f deact=%.4f -> swapped',
                    self._port_id,
                    'increasing' if direction > 0 else 'decreasing',
                    activation,
                    deactivation,
                )
                activation, deactivation = deactivation, activation
        return activation, deactivation

    @staticmethod
    def _mean_or_none(values: list[float]) -> Optional[float]:
        if not values:
            return None
        return sum(values) / len(values)

    def _reading_pressure_abs_psi(self, reading: PortReading) -> Optional[float]:
        measurement_settings = get_measurement_settings(self._config)
        pressure_abs, _source_used = select_main_pressure_abs_psi(
            reading=reading,
            settings=measurement_settings,
            barometric_psi=self._get_barometric_psi(self._port_id),
        )
        return pressure_abs

    def _absolute_to_test_reference(self, pressure_abs_psi: float) -> float:
        pressure_ref = self._test_setup.pressure_reference if self._test_setup else None
        if str(pressure_ref or '').strip().lower() == 'gauge':
            return pressure_abs_psi - self._get_barometric_psi(self._port_id)
        return pressure_abs_psi

    def _reading_pressure_test_psi(self, reading: PortReading) -> Optional[float]:
        pressure_abs = self._reading_pressure_abs_psi(reading)
        if pressure_abs is None:
            return None
        return self._absolute_to_test_reference(pressure_abs)

    def _resolve_hardware_limits_test_reference(self) -> tuple[float, float]:
        labjack_cfg = self._config.get('hardware', {}).get('labjack', {})
        port_cfg = labjack_cfg.get(self._port_id, {})
        min_abs = float(port_cfg.get('transducer_pressure_min', 0.0))
        max_abs = float(port_cfg.get('transducer_pressure_max', 115.0))
        min_ref = self._absolute_to_test_reference(min_abs)
        max_ref = self._absolute_to_test_reference(max_abs)
        if min_ref <= max_ref:
            return (min_ref, max_ref)
        return (max_ref, min_ref)

    def _safe_vent(self) -> None:
        """Safely vent to atmosphere."""
        try:
            self._port.vent_to_atmosphere()
        except Exception as exc:
            logger.error('%s: Failed to vent: %s', self._port_id, exc)

    def _ensure_alicat_units(self) -> None:
        """Best-effort sync of Alicat display units with current test setup."""
        units_code = self._test_setup.units_code if self._test_setup else None
        if not units_code:
            return
        try:
            ok = self._port.alicat.configure_units_from_ptp(units_code)
            if not ok:
                logger.warning(
                    '%s: Alicat units verify failed (requested code=%s); continuing with current controller units',
                    self._port_id,
                    units_code,
                )
        except Exception as exc:
            logger.warning(
                '%s: Failed to enforce Alicat units %s: %s; continuing with current controller units',
                self._port_id,
                units_code,
                exc,
            )

    def _set_pressure_or_raise(self, target_abs_psi: float) -> None:
        """Set pressure with one recovery retry before raising."""
        if self._port.set_pressure(target_abs_psi):
            return

        logger.warning('%s: Set pressure failed once at %.4f PSI; retrying after unit sync', self._port_id, target_abs_psi)
        self._ensure_alicat_units()
        self._port.alicat.cancel_hold()
        time.sleep(0.05)

        if self._port.set_pressure(target_abs_psi):
            return

        self._fail(
            TestFailureCode.PRESSURE_COMMAND_FAILURE,
            f'Failed to set pressure to {target_abs_psi:.4f} PSI',
        )

    def _cancel_and_emit(self) -> bool:
        if not self._cancel_event.is_set():
            return False
        self._emit_event('run_cancelled')
        self._safe_vent()
        if self._on_cancelled:
            self._on_cancelled()
        return True

    def _abort_with_error(self, failure: TestFailure) -> None:
        """Abort test execution due to failure.
        
        Order of operations:
        1. Emit failure event (for logging/tracking)
        2. Notify error callback (triggers state machine transition)
        3. Safely vent port (after error is logged/recorded)
        
        Note: For NO_SWITCH_DETECTED failures, venting should already have happened
        in the cycle phase runner, but we ensure it here as well.
        """
        logger.error(
            '%s: Test aborted with failure [%s]: %s',
            self._port_id,
            failure.code.value,
            failure.message,
        )
        self._emit_event('run_failed', code=failure.code.value, message=failure.message)
        self._notify_error(failure)
        # Vent after error is logged and state machine is notified
        # This ensures error state is set before hardware cleanup
        # For NO_SWITCH_DETECTED, venting already happened, but ensure it's done
        self._safe_vent()

    def _notify_error(self, failure: TestFailure) -> None:
        if self._on_error:
            self._on_error(f'[{failure.code.value}] {failure.message}')

    def _emit_event(self, event_type: str, **data: Any) -> None:
        if not self._on_event:
            return
        self._on_event(TestEvent(event_type=event_type, port_id=self._port_id, data=data))

    @staticmethod
    def _fail(code: TestFailureCode, message: str) -> None:
        raise TestFailure(code, message)
