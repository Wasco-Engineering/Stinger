"""Small reusable primitives for sweep and edge-detection flows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SweepResult:
    activation_psi: float
    deactivation_psi: float


@dataclass(frozen=True)
class SweepPassOutcome:
    result: Optional[SweepResult]
    missing_edge: Optional[str] = None


@dataclass(frozen=True)
class EdgeDetection:
    pressure_psi: float
    activated: bool


@dataclass(frozen=True)
class DebounceState:
    last_state: Optional[bool] = None
    pending_state: Optional[bool] = None
    pending_count: int = 0
    last_edge_time: float = 0.0
    pending_pressure: Optional[float] = None


@dataclass(frozen=True)
class SpdtDebounceState:
    """Debounce switch edges when either NO or NC changes (single-pole safe)."""

    last_no: Optional[bool] = None
    last_nc: Optional[bool] = None
    pending_no: Optional[bool] = None
    pending_nc: Optional[bool] = None
    pending_activated: Optional[bool] = None
    pending_count: int = 0
    pending_pressure: Optional[float] = None
    last_edge_time: float = 0.0
    committed_activated: Optional[bool] = None


def collapse_switch_activated(*, no_active: bool, nc_active: bool) -> bool:
    """Map NO/NC reads to tripped vs not-tripped for cycling/precision edges."""
    if no_active != nc_active:
        return bool(no_active and not nc_active)
    if no_active:
        return True
    if nc_active:
        return False
    return False


def observe_spdt_transition(
    state: SpdtDebounceState,
    no_active: bool,
    nc_active: bool,
    stable_count: int,
    min_edge_interval_s: float,
    now_s: float,
    *,
    current_pressure: Optional[float] = None,
) -> tuple[SpdtDebounceState, Optional[bool], Optional[float]]:
    """Commit an edge when either NO or NC is stable after a terminal change."""
    activated = collapse_switch_activated(no_active=no_active, nc_active=nc_active)
    pair = (no_active, nc_active)

    if state.last_no is None:
        return (
            SpdtDebounceState(
                last_no=no_active,
                last_nc=nc_active,
                committed_activated=activated,
            ),
            None,
            None,
        )

    pending_no = state.pending_no
    pending_nc = state.pending_nc
    pending_activated = state.pending_activated
    pending_count = state.pending_count
    pending_pressure = state.pending_pressure
    last_edge_time = state.last_edge_time
    committed_activated = state.committed_activated
    last_no = state.last_no
    last_nc = state.last_nc

    terminals_changed = pair != (state.last_no, state.last_nc)
    pending_pair = (pending_no, pending_nc) if pending_no is not None and pending_nc is not None else None

    if terminals_changed:
        if pending_pair != pair:
            pending_no, pending_nc = no_active, nc_active
            pending_activated = activated
            pending_count = 1
            pending_pressure = current_pressure
        else:
            pending_count += 1
    else:
        pending_no = None
        pending_nc = None
        pending_activated = None
        pending_count = 0
        pending_pressure = None

    committed_edge: Optional[bool] = None
    committed_pressure: Optional[float] = None
    if (
        pending_activated is not None
        and pending_count >= stable_count
        and now_s - last_edge_time >= min_edge_interval_s
    ):
        committed_edge = pending_activated
        committed_pressure = pending_pressure
        committed_activated = pending_activated
        last_no, last_nc = pending_no, pending_nc
        pending_no = None
        pending_nc = None
        pending_activated = None
        pending_count = 0
        pending_pressure = None
        last_edge_time = now_s

    return (
        SpdtDebounceState(
            last_no=last_no if committed_edge is not None else state.last_no,
            last_nc=last_nc if committed_edge is not None else state.last_nc,
            pending_no=pending_no,
            pending_nc=pending_nc,
            pending_activated=pending_activated,
            pending_count=pending_count,
            pending_pressure=pending_pressure,
            last_edge_time=last_edge_time,
            committed_activated=committed_activated,
        ),
        committed_edge,
        committed_pressure,
    )


def resolve_sweep_result(
    edge_out: EdgeDetection,
    edge_back: EdgeDetection,
) -> Optional[SweepResult]:
    activation = (
        edge_out.pressure_psi
        if edge_out.activated
        else edge_back.pressure_psi
        if edge_back.activated
        else None
    )
    deactivation = (
        edge_out.pressure_psi
        if not edge_out.activated
        else edge_back.pressure_psi
        if not edge_back.activated
        else None
    )
    if activation is None or deactivation is None:
        return None
    return SweepResult(activation_psi=activation, deactivation_psi=deactivation)


def observe_debounced_transition(
    state: DebounceState,
    current_state: bool,
    stable_count: int,
    min_edge_interval_s: float,
    now_s: float,
    *,
    track_last_sample: bool,
    update_edge_time_on_reject: bool,
    current_pressure: Optional[float] = None,
) -> tuple[DebounceState, Optional[bool], Optional[float]]:
    """Update edge debounce state and optionally emit a committed edge state.

    Returns:
        A tuple of (new_state, committed_edge, committed_pressure).
        ``committed_pressure`` is the pressure recorded at the *first*
        detection of the pending state change, which is more accurate than
        the pressure at commit time during fast ramp rates.
    """
    if state.last_state is None:
        return (
            DebounceState(
                last_state=current_state,
                pending_state=state.pending_state,
                pending_count=state.pending_count,
                last_edge_time=state.last_edge_time,
                pending_pressure=state.pending_pressure,
            ),
            None,
            None,
        )

    pending_state = state.pending_state
    pending_count = state.pending_count
    last_state = state.last_state
    last_edge_time = state.last_edge_time
    pending_pressure = state.pending_pressure

    if pending_state is None:
        if current_state != last_state:
            pending_state = current_state
            pending_count = 1
            # Capture the pressure at first detection of the state change
            pending_pressure = current_pressure
    else:
        if current_state == pending_state:
            pending_count += 1
        else:
            pending_state = current_state
            pending_count = 1
            # Reset pressure to current on direction change
            pending_pressure = current_pressure

    committed_edge: Optional[bool] = None
    committed_pressure: Optional[float] = None
    if pending_state is not None and pending_count >= stable_count:
        if now_s - last_edge_time >= min_edge_interval_s:
            committed_edge = pending_state
            committed_pressure = pending_pressure
            last_state = pending_state
            pending_state = None
            pending_count = 0
            pending_pressure = None
            last_edge_time = now_s
        elif update_edge_time_on_reject:
            last_edge_time = now_s

    if track_last_sample:
        last_state = current_state

    return (
        DebounceState(
            last_state=last_state,
            pending_state=pending_state,
            pending_count=pending_count,
            last_edge_time=last_edge_time,
            pending_pressure=pending_pressure,
        ),
        committed_edge,
        committed_pressure,
    )
