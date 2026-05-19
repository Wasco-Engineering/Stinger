"""
Per-port state machine for test workflow execution.

Each port has its own independent state machine that manages the test sequence
from pressurization through evaluation and recording.
"""

import logging
import time
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from transitions import Machine, MachineError

logger = logging.getLogger(__name__)


class PortState(Enum):
    """Top-level states for a test port."""
    INIT = "init"
    IDLE = "idle"
    PRESSURIZING = "pressurizing"
    MANUAL_ADJUST = "manual_adjust"  # QAL15 only
    CYCLING = "cycling"
    PRECISION_TEST = "precision_test"
    REVIEW = "review"
    ERROR = "error"
    END = "end"


class PortSubstate(Enum):
    """Substates within each major state."""
    # INIT substates
    INIT_CONNECTING = "init.connecting"
    INIT_READY = "init.ready"
    
    # PRESSURIZING substates
    PRESS_START = "pressurizing.start"
    PRESS_RAMPING = "pressurizing.ramping"
    PRESS_HOLD = "pressurizing.hold"
    PRESS_VENT = "pressurizing.vent"
    
    # MANUAL_ADJUST substates
    MANUAL_WAITING = "manual_adjust.waiting_switch"
    MANUAL_DETECTED = "manual_adjust.switch_detected"
    
    # CYCLING substates
    CYCLE_START = "cycling.start"
    CYCLE_SETPOINT = "cycling.setpoint"
    CYCLE_RETURN = "cycling.return"
    CYCLE_NEXT = "cycling.next"
    
    # PRECISION_TEST substates
    PREC_START = "precision.start"
    PREC_FAST_APPROACH = "precision.fast_approach"
    PREC_SLOW_SWEEP_1 = "precision.slow_sweep_1"
    PREC_EDGE_1 = "precision.edge_1_detected"
    PREC_OVERSHOOT = "precision.overshoot"
    PREC_SLOW_SWEEP_2 = "precision.slow_sweep_2"
    PREC_EDGE_2 = "precision.edge_2_detected"
    PREC_EXHAUST = "precision.exhaust"
    
    # REVIEW substates
    REVIEW_EVALUATING = "review.evaluating"
    REVIEW_SHOWING = "review.showing_result"
    REVIEW_WAITING = "review.waiting_decision"
    REVIEW_RECORDING = "review.recording"
    
    # ERROR substates
    ERROR_HARDWARE = "error.hardware_fault"
    ERROR_EDGE_NOT_FOUND = "error.edge_not_found"
    ERROR_NO_SWITCH = "error.no_switch_detected"
    ERROR_WIRING = "error.wiring_fault"
    ERROR_DB = "error.db_write_failed"


class PortStateMachine(QObject):
    """
    State machine for a single test port.
    
    Manages the test workflow from start to finish, with signals for
    UI updates and hardware control callbacks.
    """
    
    # Signals for UI updates
    state_changed = pyqtSignal(str, str, dict)  # (port_id, state, data)
    substate_changed = pyqtSignal(str, str, dict)  # (port_id, substate, data)
    button_state_changed = pyqtSignal(str, dict)  # (port_id, {primary, cancel})
    test_result_ready = pyqtSignal(str, dict)  # (port_id, result_data)
    error_occurred = pyqtSignal(str, str)  # (port_id, error_message)
    
    # Top-level states for transitions library
    STATES = [s.value for s in PortState]
    
    def __init__(self, port_id: str):
        """
        Initialize state machine for a port.
        
        Args:
            port_id: Identifier for this port ('port_a' or 'port_b').
        """
        super().__init__()
        self.port_id = port_id
        
        # Current state tracking
        self._substate: Optional[PortSubstate] = None
        self._last_error: str = ""
        
        # Test context
        self._workflow_type: str = "QAL15"  # QAL15, QAL16, or QAL17
        self._current_cycle: int = 0
        self._max_cycles: int = 3
        self._attempt_count: int = 0
        self._max_attempts: int = 3
        
        # Measurement results
        self._increasing_activation: Optional[float] = None
        self._decreasing_deactivation: Optional[float] = None
        self._in_spec: Optional[bool] = None
        
        # Switch state tracking
        self._switch_has_changed: bool = False
        
        # Build state machine
        self._build_machine()
        
        logger.info(f"PortStateMachine created for {port_id}")
    
    def _build_machine(self) -> None:
        """Build the transitions state machine."""
        
        transitions = [
            # INIT transitions
            {'trigger': 'initialize_complete', 'source': PortState.INIT.value, 
             'dest': PortState.IDLE.value, 'after': '_on_enter_idle'},
            {'trigger': 'initialize_failed', 'source': PortState.INIT.value,
             'dest': PortState.ERROR.value, 'after': '_on_enter_error'},
            
            # IDLE transitions
            {'trigger': 'start_pressurize', 'source': PortState.IDLE.value,
             'dest': PortState.PRESSURIZING.value, 'after': '_on_enter_pressurizing'},
            {'trigger': 'start_test', 'source': PortState.IDLE.value,
             'dest': PortState.CYCLING.value, 'after': '_on_enter_cycling',
             'conditions': '_is_qal16_or_17'},
            {'trigger': 'end_work_order', 'source': PortState.IDLE.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # PRESSURIZING transitions
            {'trigger': 'pressure_reached', 'source': PortState.PRESSURIZING.value,
             'dest': PortState.MANUAL_ADJUST.value, 'after': '_on_enter_manual_adjust',
             'conditions': '_is_qal15'},
            {'trigger': 'pressure_reached', 'source': PortState.PRESSURIZING.value,
             'dest': PortState.CYCLING.value, 'after': '_on_enter_cycling',
             'conditions': '_is_qal16_or_17'},
            {'trigger': 'vent', 'source': PortState.PRESSURIZING.value,
             'dest': PortState.IDLE.value, 'before': '_action_vent', 'after': '_on_enter_idle'},
            {'trigger': 'end_work_order', 'source': PortState.PRESSURIZING.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # MANUAL_ADJUST transitions
            {'trigger': 'switch_changed', 'source': PortState.MANUAL_ADJUST.value,
             'dest': None, 'after': '_on_switch_detected'},  # Internal transition
            {'trigger': 'start_test', 'source': PortState.MANUAL_ADJUST.value,
             'dest': PortState.CYCLING.value, 'after': '_on_enter_cycling',
             'conditions': '_switch_ready'},
            {'trigger': 'vent', 'source': PortState.MANUAL_ADJUST.value,
             'dest': PortState.IDLE.value, 'before': '_action_vent', 'after': '_on_enter_idle'},
            {'trigger': 'end_work_order', 'source': PortState.MANUAL_ADJUST.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # CYCLING transitions
            {'trigger': 'cycles_complete', 'source': PortState.CYCLING.value,
             'dest': PortState.PRECISION_TEST.value, 'after': '_on_enter_precision'},
            {'trigger': 'cancel', 'source': PortState.CYCLING.value,
             'dest': PortState.IDLE.value, 'before': '_action_vent', 'after': '_on_enter_idle'},
            {'trigger': 'error', 'source': PortState.CYCLING.value,
             'dest': PortState.ERROR.value, 'after': '_on_enter_error'},
            {'trigger': 'end_work_order', 'source': PortState.CYCLING.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # PRECISION_TEST transitions
            {'trigger': 'edges_captured', 'source': PortState.PRECISION_TEST.value,
             'dest': PortState.REVIEW.value, 'after': '_on_enter_review'},
            {'trigger': 'edge_not_found', 'source': PortState.PRECISION_TEST.value,
             'dest': PortState.ERROR.value, 'after': '_on_enter_error'},
            {'trigger': 'cancel', 'source': PortState.PRECISION_TEST.value,
             'dest': PortState.IDLE.value, 'before': '_action_vent', 'after': '_on_enter_idle'},
            {'trigger': 'end_work_order', 'source': PortState.PRECISION_TEST.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # REVIEW transitions
            {'trigger': 'record_success', 'source': PortState.REVIEW.value,
             'dest': PortState.IDLE.value, 'before': '_action_record', 'after': '_on_enter_idle'},
            {'trigger': 'record_failure', 'source': PortState.REVIEW.value,
             'dest': PortState.IDLE.value, 'before': '_action_record', 'after': '_on_enter_idle'},
            {'trigger': 'retest', 'source': PortState.REVIEW.value,
             'dest': PortState.PRESSURIZING.value, 'after': '_on_retest',
             'conditions': '_is_qal15'},
            {'trigger': 'retest', 'source': PortState.REVIEW.value,
             'dest': PortState.CYCLING.value, 'after': '_on_retest',
             'conditions': '_is_qal16_or_17'},
            {'trigger': 'end_work_order', 'source': PortState.REVIEW.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # ERROR transitions
            {'trigger': 'reset', 'source': PortState.ERROR.value,
             'dest': PortState.IDLE.value, 'before': '_action_vent', 'after': '_on_enter_idle'},
            {'trigger': 'end_work_order', 'source': PortState.ERROR.value,
             'dest': PortState.END.value, 'before': '_action_vent', 'after': '_on_enter_end'},
            
            # END transitions
            {'trigger': 'logout_complete', 'source': PortState.END.value,
             'dest': PortState.INIT.value},
            
            # Universal error transition
            {'trigger': 'error', 'source': '*',
             'dest': PortState.ERROR.value, 'after': '_on_enter_error'},
        ]
        
        self.machine = Machine(
            model=self,
            states=self.STATES,
            transitions=transitions,
            initial=PortState.INIT.value,
            after_state_change=self._on_state_change,
            send_event=True,
            queued=True
        )
    
    # -------------------------------------------------------------------------
    # Condition methods
    # -------------------------------------------------------------------------
    
    def _is_qal15(self, event=None) -> bool:
        """Check if current workflow is QAL15."""
        return self._workflow_type == "QAL15"
    
    def _is_qal16_or_17(self, event=None) -> bool:
        """Check if current workflow is QAL16 or QAL17."""
        return self._workflow_type in ("QAL16", "QAL17")
    
    def _switch_ready(self, event=None) -> bool:
        """Check if switch has been detected (for manual adjust gating)."""
        return self._switch_has_changed
    
    # -------------------------------------------------------------------------
    # Action methods
    # -------------------------------------------------------------------------
    
    def _action_vent(self, event=None) -> None:
        """Vent port to atmosphere."""
        logger.info(f"{self.port_id}: Venting to atmosphere")
        # Hardware control is done by the controller, not the state machine
    
    def _action_record(self, event=None) -> None:
        """Record test result to database."""
        logger.info(f"{self.port_id}: Recording result - InSpec={self._in_spec}")
        # Actual DB write is done by the controller
    
    # -------------------------------------------------------------------------
    # State entry handlers
    # -------------------------------------------------------------------------
    
    def _on_state_change(self, event) -> None:
        """Called after any state transition."""
        new_state = self.state
        logger.debug(f"{self.port_id}: State -> {new_state}")
        
        data = {
            'workflow': self._workflow_type,
            'attempt': self._attempt_count,
            'cycle': self._current_cycle,
        }
        
        self.state_changed.emit(self.port_id, new_state, data)
        self._update_button_state()
    
    def _on_enter_idle(self, event=None) -> None:
        """Enter IDLE state."""
        self._substate = None
        self._switch_has_changed = False
        self._current_cycle = 0
    
    def _on_enter_pressurizing(self, event=None) -> None:
        """Enter PRESSURIZING state."""
        self._substate = PortSubstate.PRESS_START
        self._update_substate()
    
    def _on_enter_manual_adjust(self, event=None) -> None:
        """Enter MANUAL_ADJUST state."""
        self._substate = PortSubstate.MANUAL_WAITING
        self._switch_has_changed = False
        self._update_substate()
    
    def _on_switch_detected(self, event=None) -> None:
        """Handle switch state change during manual adjust."""
        self._switch_has_changed = True
        self._substate = PortSubstate.MANUAL_DETECTED
        self._update_substate()
        self._update_button_state()
    
    def _on_enter_cycling(self, event=None) -> None:
        """Enter CYCLING state."""
        self._substate = PortSubstate.CYCLE_START
        self._current_cycle = 0
        self._update_substate()
    
    def _on_enter_precision(self, event=None) -> None:
        """Enter PRECISION_TEST state."""
        self._substate = PortSubstate.PREC_START
        self._increasing_activation = None
        self._decreasing_deactivation = None
        self._in_spec = None
        self._update_substate()
    
    def _on_enter_review(self, event=None) -> None:
        """Enter REVIEW state."""
        self._substate = PortSubstate.REVIEW_EVALUATING
        self._evaluate_result()
        self._update_substate()
    
    def _on_enter_error(self, event=None) -> None:
        """Enter ERROR state."""
        if event and hasattr(event, 'kwargs'):
            error_message = event.kwargs.get('message', 'Unknown error')
            self._last_error = error_message
        else:
            # Preserve existing error message if no new one provided
            if not self._last_error:
                self._last_error = 'Unknown error'
        
        # Determine appropriate error substate based on error message
        error_lower = self._last_error.lower()
        if 'no switch detected' in error_lower or 'no_switch_detected' in error_lower:
            self._substate = PortSubstate.ERROR_NO_SWITCH
        elif 'edge_not_found' in error_lower or 'edge not detected' in error_lower:
            self._substate = PortSubstate.ERROR_EDGE_NOT_FOUND
        elif 'wiring' in error_lower or 'no_active' in error_lower or 'nc_active' in error_lower:
            self._substate = PortSubstate.ERROR_WIRING
        elif 'database' in error_lower or 'db' in error_lower or 'write' in error_lower:
            self._substate = PortSubstate.ERROR_DB
        else:
            self._substate = PortSubstate.ERROR_HARDWARE
        
        logger.error(
            f"{self.port_id}: Error [{self._substate.value}] - {self._last_error}"
        )
        self._update_substate()
        self.error_occurred.emit(self.port_id, self._last_error)
    
    def _on_enter_end(self, event=None) -> None:
        """Enter END state."""
        logger.info(f"{self.port_id}: Work order ending")
    
    def _on_retest(self, event=None) -> None:
        """Handle retest - increment attempt counter."""
        self._attempt_count += 1
        logger.info(f"{self.port_id}: Retest - attempt {self._attempt_count}")
    
    # -------------------------------------------------------------------------
    # Helper methods
    # -------------------------------------------------------------------------
    
    def _update_substate(self) -> None:
        """Emit substate change signal."""
        if self._substate:
            self.substate_changed.emit(self.port_id, self._substate.value, {})
    
    def _update_button_state(self) -> None:
        """Update button labels/enabled states based on current state."""
        state = self.state
        substate = self._substate
        
        primary = {'label': '', 'enabled': False, 'color': 'default', 'action': None, 'blink': False}
        cancel = {'label': '', 'enabled': False, 'action': None}
        
        if state == PortState.IDLE.value:
            if self._is_qal16_or_17():
                primary = {'label': 'Test', 'enabled': True, 'color': 'green', 'action': 'start_test', 'blink': False}
            else:
                primary = {'label': 'Pressurize', 'enabled': True, 'color': 'green', 'action': 'start_pressurize', 'blink': False}
            cancel = {'label': '', 'enabled': False, 'action': None}
            
        elif state == PortState.PRESSURIZING.value:
            primary = {'label': 'Pressurizing…', 'enabled': False, 'color': 'yellow', 'action': None, 'blink': False}
            cancel = {'label': 'Vent', 'enabled': True, 'action': 'vent'}
            
        elif state == PortState.MANUAL_ADJUST.value:
            if self._switch_has_changed:
                primary = {'label': 'Test', 'enabled': True, 'color': 'blue', 'action': 'start_test', 'blink': False}
            else:
                primary = {'label': 'Test', 'enabled': False, 'color': 'default', 'action': 'start_test', 'blink': False}
            cancel = {'label': 'Vent', 'enabled': True, 'action': 'vent'}
            
        elif state == PortState.CYCLING.value:
            primary = {'label': 'Cycling…', 'enabled': False, 'color': 'yellow', 'action': None, 'blink': True}
            cancel = {'label': 'Cancel', 'enabled': True, 'action': 'cancel'}
            
        elif state == PortState.PRECISION_TEST.value:
            primary = {'label': 'Testing…', 'enabled': False, 'color': 'yellow', 'action': None, 'blink': True}
            cancel = {'label': 'Cancel', 'enabled': True, 'action': 'cancel'}
            
        elif state == PortState.REVIEW.value:
            if self._in_spec:
                primary = {'label': 'Record Success', 'enabled': True, 'color': 'green', 'action': 'record_success', 'blink': False}
                cancel = {'label': 'Retest', 'enabled': True, 'action': 'retest'}
            else:
                if self._attempt_count < self._max_attempts - 1:
                    # Attempts 1 and 2: Retest is primary
                    primary = {'label': 'Retest', 'enabled': True, 'color': 'default', 'action': 'retest', 'blink': False}
                    cancel = {'label': 'Record Failure', 'enabled': True, 'action': 'record_failure'}
                else:
                    # Attempt 3: Record Failure is primary (swap positions)
                    primary = {'label': 'Record Failure', 'enabled': True, 'color': 'red', 'action': 'record_failure', 'blink': False}
                    cancel = {'label': 'Retest', 'enabled': True, 'action': 'retest'}
                    
        elif state == PortState.ERROR.value:
            # Customize error message based on substate
            if self._substate == PortSubstate.ERROR_NO_SWITCH:
                primary = {'label': 'No Switch Detected - Retest', 'enabled': True, 'color': 'yellow', 'action': 'reset', 'blink': False}
            else:
                primary = {'label': 'Test Failed - Try Again', 'enabled': True, 'color': 'default', 'action': 'reset', 'blink': False}
            cancel = {'label': '', 'enabled': False, 'action': None}
        
        self.button_state_changed.emit(self.port_id, {
            'primary': primary,
            'cancel': cancel,
        })
    
    def _evaluate_result(self) -> None:
        """Evaluate test result against acceptance bands."""
        if self._in_spec is None:
            self._in_spec = (
                self._increasing_activation is not None and
                self._decreasing_deactivation is not None
            )

        # Band-checking will be done by the controller with PTP data;
        # the state machine just checks that measurements exist.
        self._substate = PortSubstate.REVIEW_SHOWING
        
        result = {
            'increasing_activation': self._increasing_activation,
            'decreasing_deactivation': self._decreasing_deactivation,
            'in_spec': self._in_spec,
            'attempt': self._attempt_count,
        }
        self.test_result_ready.emit(self.port_id, result)
    
    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------
    
    def set_workflow_type(self, workflow: str) -> None:
        """Set the workflow type (QAL15, QAL16, or QAL17)."""
        if workflow in ("QAL15", "QAL16", "QAL17"):
            self._workflow_type = workflow
            logger.info(f"{self.port_id}: Workflow set to {workflow}")
    
    def set_measurements(
        self, 
        increasing_activation: float,
        decreasing_deactivation: float,
        in_spec: Optional[bool] = None,
    ) -> None:
        """Set the measured activation/deactivation values."""
        self._increasing_activation = increasing_activation
        self._decreasing_deactivation = decreasing_deactivation
        self._in_spec = in_spec
    
    def reset_for_new_unit(self) -> None:
        """Reset state for testing a new unit."""
        self._attempt_count = 0
        self._increasing_activation = None
        self._decreasing_deactivation = None
        self._in_spec = None
        self._switch_has_changed = False
    
    def trigger(self, event_name: str, **kwargs) -> bool:
        """
        Trigger a state machine event.
        
        Args:
            event_name: Name of the event/trigger.
            **kwargs: Additional arguments to pass to the transition.
            
        Returns:
            True if transition was successful.
        """
        if hasattr(self, event_name):
            try:
                getattr(self, event_name)(**kwargs)
                return True
            except MachineError as e:
                logger.warning(f"{self.port_id}: Cannot trigger '{event_name}' in state '{self.state}': {e}")
                return False
        else:
            logger.error(f"{self.port_id}: Unknown event '{event_name}'")
            return False
    
    def can_trigger(self, event_name: str) -> bool:
        """Check if an event can be triggered in the current state."""
        return event_name in self.machine.get_triggers(self.state)
    
    @property
    def current_state(self) -> str:
        """Get the current state."""
        return self.state
    
    @property
    def current_substate(self) -> Optional[str]:
        """Get the current substate."""
        return self._substate.value if self._substate else None
