"""
Tests for the port state machine.
"""

import pytest

from app.services.state.port_state_machine import PortStateMachine, PortState


class TestPortStateMachine:
    """Test cases for PortStateMachine."""
    
    def test_initial_state(self):
        """State machine starts in INIT state."""
        sm = PortStateMachine("port_a")
        assert sm.current_state == PortState.INIT.value
    
    def test_initialization_flow(self):
        """State machine transitions from INIT to IDLE."""
        sm = PortStateMachine("port_a")
        
        result = sm.trigger("initialize_complete")
        assert result is True
        assert sm.current_state == PortState.IDLE.value
    
    def test_qal15_flow_requires_switch(self):
        """QAL15 requires switch change before Test button is enabled."""
        sm = PortStateMachine("port_a")
        sm.set_workflow_type("QAL15")
        
        # Initialize and go to pressurizing
        sm.trigger("initialize_complete")
        sm.trigger("start_pressurize")
        assert sm.current_state == PortState.PRESSURIZING.value
        
        # Reach target pressure
        sm.trigger("pressure_reached")
        assert sm.current_state == PortState.MANUAL_ADJUST.value
        
        # Cannot start test until switch changes
        assert not sm._switch_ready()
        
        # Switch changes
        sm.trigger("switch_changed")
        assert sm._switch_ready()
        
        # Now can start test
        result = sm.trigger("start_test")
        assert result is True
        assert sm.current_state == PortState.CYCLING.value
    
    def test_qal16_skips_manual_adjust(self):
        """QAL16 goes directly from pressurizing to cycling."""
        sm = PortStateMachine("port_a")
        sm.set_workflow_type("QAL16")
        
        sm.trigger("initialize_complete")
        sm.trigger("start_pressurize")
        sm.trigger("pressure_reached")
        
        # Should go directly to cycling (not manual adjust)
        assert sm.current_state == PortState.CYCLING.value
    
    def test_vent_returns_to_idle(self):
        """Vent from any running state returns to IDLE."""
        sm = PortStateMachine("port_a")
        sm.trigger("initialize_complete")
        sm.trigger("start_pressurize")
        
        assert sm.current_state == PortState.PRESSURIZING.value
        
        sm.trigger("vent")
        assert sm.current_state == PortState.IDLE.value
    
    def test_error_and_reset(self):
        """Error state can be reset to IDLE."""
        sm = PortStateMachine("port_a")
        sm.trigger("initialize_complete")
        
        sm.trigger("error", message="Test error")
        assert sm.current_state == PortState.ERROR.value
        
        sm.trigger("reset")
        assert sm.current_state == PortState.IDLE.value
    
    def test_retest_increments_attempt(self):
        """Retest should increment attempt counter."""
        sm = PortStateMachine("port_a")
        sm.set_workflow_type("QAL16")
        
        sm.trigger("initialize_complete")
        sm.trigger("start_pressurize")
        sm.trigger("pressure_reached")
        sm.trigger("cycles_complete")
        sm.trigger("edges_captured")
        
        assert sm.current_state == PortState.REVIEW.value
        assert sm._attempt_count == 0
        
        sm.trigger("retest")
        assert sm._attempt_count == 1
    
    def test_button_state_updates(self):
        """Button states should update based on state."""
        sm = PortStateMachine("port_a")
        
        button_states = []
        sm.button_state_changed.connect(lambda port, data: button_states.append(data))
        
        sm.trigger("initialize_complete")
        
        # Should have received button state update
        assert len(button_states) > 0
        last_state = button_states[-1]
        assert last_state['primary']['label'] == 'Pressurize'
        assert last_state['primary']['enabled'] is True

    def test_explicit_in_spec_value_is_preserved(self):
        """Controller-provided in-spec decision should drive review state."""
        sm = PortStateMachine('port_a')
        sm.set_workflow_type('QAL16')

        sm.trigger('initialize_complete')
        sm.trigger('start_test')
        sm.trigger('cycles_complete')
        sm.set_measurements(10.0, 12.0, in_spec=False)
        sm.trigger('edges_captured')

        assert sm.current_state == PortState.REVIEW.value
        assert sm._in_spec is False

    def test_final_record_failure_button_is_red(self):
        """Final out-of-spec review should make Record Failure visibly red."""
        sm = PortStateMachine('port_a')
        sm.set_workflow_type('QAL16')

        button_states = []
        sm.button_state_changed.connect(lambda _port, data: button_states.append(data))

        sm.trigger('initialize_complete')
        sm.trigger('start_test')

        for _attempt in range(2):
            sm.trigger('cycles_complete')
            sm.set_measurements(10.0, 12.0, in_spec=False)
            sm.trigger('edges_captured')
            sm.trigger('retest')

        sm.trigger('cycles_complete')
        sm.set_measurements(10.0, 12.0, in_spec=False)
        sm.trigger('edges_captured')

        last_state = button_states[-1]
        assert last_state['primary']['label'] == 'Record Failure'
        assert last_state['primary']['action'] == 'record_failure'
        assert last_state['primary']['color'] == 'red'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
