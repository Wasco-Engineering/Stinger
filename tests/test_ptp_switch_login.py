from __future__ import annotations

from types import MethodType, SimpleNamespace
from typing import Any

from app.services import work_order_controller
from app.services.state.port_state_machine import PortState
from app.services.work_order_controller import WorkOrderController


class _FailingPort:
    last_switch_resolution = SimpleNamespace(errors=('PTP NO/NC terminals are not observable',))

    def configure_from_ptp(self, _ptp_params: dict[str, Any]) -> bool:
        return False


class _FakePortManager:
    def get_port(self, _port_id: object) -> _FailingPort:
        return _FailingPort()


class _FakeUiBridge:
    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.work_orders: list[dict[str, Any]] = []

    def set_work_order(self, work_order: dict[str, Any]) -> None:
        self.work_orders.append(work_order)

    def update_progress(self, *_args: Any) -> None:
        return None

    def show_error_message(self, title: str, message: str) -> None:
        self.errors.append((title, message))


class _FakeStateMachine:
    current_state = PortState.INIT.value

    def __init__(self) -> None:
        self.triggers: list[tuple[str, dict[str, Any]]] = []

    def set_workflow_type(self, _workflow_type: str) -> None:
        return None

    def reset_for_new_unit(self) -> None:
        return None

    def trigger(self, event_name: str, **kwargs: Any) -> bool:
        self.triggers.append((event_name, kwargs))
        if event_name == 'error':
            self.current_state = PortState.ERROR.value
        return True


def test_login_blocks_testing_when_ptp_switch_resolution_fails(monkeypatch) -> None:
    controller = WorkOrderController.__new__(WorkOrderController)
    controller._ui_bridge = _FakeUiBridge()
    controller._port_manager = _FakePortManager()
    controller._state_machines = {'port_a': _FakeStateMachine()}
    controller._current_test_setup = None

    def _load_ptp(self: WorkOrderController, *_args: Any, **_kwargs: Any) -> None:
        self._current_test_setup = SimpleNamespace(raw={'CommonTerminal': '4'})

    controller._load_ptp = MethodType(_load_ptp, controller)
    controller._allocate_initial_serials = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError('serial allocation should be blocked')
    )
    monkeypatch.setattr(
        work_order_controller,
        'get_work_order_progress',
        lambda *_args, **_kwargs: {'completed': 0, 'passed': 0, 'failed': 0},
    )

    controller._on_login_requested(
        {
            'OperatorID': 'OP',
            'ShopOrder': 'WO',
            'PartID': 'SPS02209-02',
            'SequenceID': '300',
            'OrderQTY': 1,
            'WOValidated': True,
        }
    )

    sm = controller._state_machines['port_a']
    assert sm.current_state == PortState.ERROR.value
    assert sm.triggers[-1][0] == 'error'
    assert controller._ui_bridge.errors
    assert controller._ui_bridge.errors[-1][0] == 'PTP Switch Configuration'
