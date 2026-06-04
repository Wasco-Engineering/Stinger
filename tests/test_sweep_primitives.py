"""Unit tests for sweep edge debounce helpers."""

from app.services.sweep_primitives import (
    SpdtDebounceState,
    collapse_switch_activated,
    observe_spdt_transition,
)


def test_collapse_spdt_no_tripped() -> None:
    assert collapse_switch_activated(no_active=True, nc_active=False) is True


def test_collapse_spdt_released() -> None:
    assert collapse_switch_activated(no_active=False, nc_active=True) is False


def test_collapse_single_pole_no_only() -> None:
    assert collapse_switch_activated(no_active=True, nc_active=True) is True
    assert collapse_switch_activated(no_active=False, nc_active=False) is False


def test_collapse_single_pole_nc_only() -> None:
    assert collapse_switch_activated(no_active=False, nc_active=True) is False
    assert collapse_switch_activated(no_active=True, nc_active=False) is True


def test_observe_spdt_commits_on_no_toggle() -> None:
    state = SpdtDebounceState(last_no=False, last_nc=True, committed_activated=False)
    stable = 2
    state, edge, pressure = observe_spdt_transition(
        state,
        True,
        False,
        stable,
        0.0,
        1.0,
        current_pressure=10.0,
    )
    assert edge is None
    state, edge, pressure = observe_spdt_transition(
        state,
        True,
        False,
        stable,
        0.0,
        1.1,
        current_pressure=10.1,
    )
    assert edge is True
    assert pressure == 10.0


def test_observe_spdt_commits_on_nc_toggle_only() -> None:
    """NC line change alone should still commit when NO stays idle."""
    state = SpdtDebounceState(last_no=False, last_nc=False, committed_activated=False)
    stable = 2
    state, edge, _ = observe_spdt_transition(
        state, False, True, stable, 0.0, 1.0, current_pressure=5.0
    )
    assert edge is None
    state, edge, pressure = observe_spdt_transition(
        state, False, True, stable, 0.0, 1.1, current_pressure=5.1
    )
    assert edge is False
    assert pressure == 5.0
