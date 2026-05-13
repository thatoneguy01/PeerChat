"""
JoinLifecycleTracer (Dapper-style tracing)
"""

import time
import logging
from uuid import uuid4
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class Span:
    name: str
    timestamp: float
    metadata: dict = field(default_factory=dict)

@dataclass
class TraceRecord:
    trace_id: str
    user_id: str
    started_at: float
    spans: list[Span] = field(default_factory=list)
    completed_at: float | None = None
    total_duration_ms: float | None = None

class JoinLifecycleTracer:
    def __init__(self):
        self._active_traces: dict[str, TraceRecord] = {}
        self._completed_traces: list[TraceRecord] = []

    def start_join_trace(self, user_id: str) -> str:
        trace_id = uuid4().hex
        now = time.time()
        self._active_traces[trace_id] = TraceRecord(
            trace_id=trace_id,
            user_id=user_id,
            started_at=now,
            spans=[Span(name="join_requested", timestamp=now)]
        )
        return trace_id

    def record_span(self, trace_id: str, span_name: str, **metadata) -> None:
        trace = self._active_traces.get(trace_id)
        if not trace:
            return
        trace.spans.append(Span(name=span_name, timestamp=time.time(), metadata=metadata))

    def complete_trace(self, trace_id: str) -> TraceRecord | None:
        trace = self._active_traces.pop(trace_id, None)
        if not trace:
            return None
        trace.completed_at = time.time()
        trace.total_duration_ms = (trace.completed_at - trace.started_at) * 1000
        self._completed_traces.append(trace)
        return trace

    def get_slow_joins(self, threshold_ms: float = 5000.0) -> list[TraceRecord]:
        return [t for t in self._completed_traces if t.total_duration_ms and t.total_duration_ms >= threshold_ms]

    def get_recent_traces(self, limit: int = 50) -> list[TraceRecord]:
        return self._completed_traces[-limit:]
