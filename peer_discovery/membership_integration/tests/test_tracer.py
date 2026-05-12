import time
from peer_discovery.membership_integration.tracer import JoinLifecycleTracer

def test_trace_lifecycle():
    tracer = JoinLifecycleTracer()
    trace_id = tracer.start_join_trace("user1")
    
    tracer.record_span(trace_id, "test_span", key="value")
    trace = tracer.complete_trace(trace_id)
    
    assert trace is not None
    assert trace.trace_id == trace_id
    assert trace.user_id == "user1"
    assert len(trace.spans) == 2
    assert trace.spans[0].name == "join_requested"
    assert trace.spans[1].name == "test_span"
    assert trace.spans[1].metadata == {"key": "value"}
    assert trace.completed_at is not None
    assert trace.total_duration_ms is not None

def test_get_slow_joins():
    tracer = JoinLifecycleTracer()
    trace_id = tracer.start_join_trace("user1")
    trace = tracer.complete_trace(trace_id)
    # forcefully make it slow
    trace.total_duration_ms = 6000.0
    
    slow = tracer.get_slow_joins(threshold_ms=5000.0)
    assert len(slow) == 1
    assert slow[0].trace_id == trace_id
