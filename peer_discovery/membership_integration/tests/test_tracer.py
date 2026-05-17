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


def test_tracer_integrated_through_coordinator_join_flow(tmp_path):
    """Verify that the coordinator records tracer spans through the full
    join → backfill_started → backfill_complete lifecycle."""
    from peer_discovery.membership_integration.coordinator import MembershipCoordinator

    coord = MembershipCoordinator("room1", storage_dir=str(tmp_path), enable_tracing=True)
    assert coord.tracer is not None

    result = coord.handle_join("alice", "Alice")
    assert result.accepted

    coord.handle_start_backfill("alice")
    coord.handle_complete_backfill("alice")

    # Trace should be completed with all expected spans
    traces = coord.tracer.get_recent_traces(limit=10)
    assert len(traces) == 1

    trace = traces[0]
    assert trace.user_id == "alice"
    assert trace.completed_at is not None
    span_names = [s.name for s in trace.spans]
    assert "join_requested" in span_names
    assert "join_accepted" in span_names
    assert "backfill_started" in span_names
    assert "backfill_complete" in span_names


def test_tracer_disabled_produces_no_traces(tmp_path):
    """When enable_tracing=False, no tracer is created."""
    from peer_discovery.membership_integration.coordinator import MembershipCoordinator

    coord = MembershipCoordinator("room1", storage_dir=str(tmp_path), enable_tracing=False)
    assert coord.tracer is None

    coord.handle_join("alice", "Alice")
    coord.handle_start_backfill("alice")
    coord.handle_complete_backfill("alice")
    # No crash — tracing calls are safely skipped

