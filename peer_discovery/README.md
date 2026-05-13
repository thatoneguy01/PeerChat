# Peer Discovery & Membership Module

**SJSU CMPE 275 Enterprise Applications | Final Project**

This module implements the **Peer Discovery and Membership** component of the class's peer-to-peer distributed chat system. The service acts as the central control plane for room membership, following a log-centric architecture inspired by systems like Raft, ZooKeeper, and Kafka.

---

## How It Works

The core philosophy is **complex coordination hidden behind a small API**. The service is the single source of truth for:
- Who is currently in the room.
- Whether members are actively connected or suspected of being disconnected.
- Where members are in their join lifecycle (e.g., waiting for message history backfill).

A member goes through several states during their time in the room:
`JOINING` → `BACKFILLING` → `ACTIVE` → `LEAVING` → `LEFT`

If an active member fails to send heartbeats, they enter a `SUSPECTED` state (a grace period). If they recover in time by sending a heartbeat, they return to `ACTIVE` without disruption. If they do not recover before the grace period ends, they become `DISCONNECTED`.

---

## Project Structure

```text
peer_discovery/
├── membership/                 # Core membership data models and state
│   ├── duplicate_guard.py      # Idempotency filter
│   ├── durability.py           # Snapshot and log persistence
│   ├── event_log.py            # Append-only sequence of MembershipEvents
│   ├── models.py               # Data models (MembershipEvent, MemberState, etc.)
│   └── snapshot.py             # Materialized view of current membership
├── membership_integration/     # External facing components
│   ├── coordinator.py          # Authoritative writer for mutations
│   ├── notifier.py             # ZooKeeper-style event subscriptions
│   ├── service.py              # MembershipService — the public API facade
│   └── tracer.py               # Dapper-style lifecycle observability
├── docs/                       # Architecture & Integration Guides
│   ├── OverallArchitechture.md # Deep dive into the distributed systems concepts
│   └── workstream_d_external_integration.md # Integration contract tests and instructions
├── membership/tests/           # Core unit tests (snapshot, log, models, replay, durability)
├── membership_integration/tests/ # Integration tests (coordinator, service, notifier, tracer)
└── README.md
```

---

## Setup

**Requirements:** Python 3.11+

Ensure the module is in your Python path to import components from `membership` and `membership_integration`.

---

## Running the Tests

```bash
pytest membership/tests/ -v
pytest membership_integration/tests/ -v
```

Two main test suites:

| Suite | What it covers |
|---|---|
| `membership/tests/` | Event log ordering, Snapshot state machine transitions, DuplicateGuard window, and Snapshot recovery (`test_durability.py`, `test_replay.py`). |
| `membership_integration/tests/` | Coordinator logic, `EventNotifier` dispatch behavior, `JoinLifecycleTracer` spans, and `MembershipService` facade integration. |

---

## Public API

### `MembershipService`

```python
from membership_integration.service import MembershipService

service = MembershipService(room_id="my-room")

# 1. Join a member
result = service.join_member(user_id="alice", display_name="Alice")

# 2. Leave a member voluntarily
service.leave_member(user_id="alice")

# 3. Send a liveness heartbeat
service.heartbeat_member(user_id="alice")

# 4. Get a fast read of current state
snapshot = service.get_membership_snapshot()

# 5. Subscribe to membership events
def on_event(event, delta):
    print(f"Event: {event.event_type} for {event.user_id}")
handle = service.subscribe_membership_events(on_event)

# 6. Signal backfill start (used by History Team)
service.start_history_backfill(user_id="alice")

# 7. Signal backfill complete (used by History Team)
service.complete_history_backfill(user_id="alice")
```

| Method | Purpose |
|---|---|
| `join_member(user_id, name)` | Request to join the room. Returns `JoinResult`. |
| `leave_member(user_id)` | Voluntary leave. Appends `LEAVE_REQUESTED` and `LEAVE_CONFIRMED`. |
| `heartbeat_member(user_id)` | Periodic liveness signal from a connected peer. |
| `get_membership_snapshot()` | Fast read: returns current members, their states, and membership version. |
| `subscribe_membership_events()` | Watch-style subscription for membership change notifications. |
| `start_history_backfill()` | Signal from history team: backfill has begun for this user. |
| `complete_history_backfill()`| Signal from history team: backfill is done, user becomes `ACTIVE`. |

---

## Integration with Other Teams

Short version below. Full contracts and guides live in `docs/workstream_d_external_integration.md`.

### Message Distribution Team

- Call `get_membership_snapshot()` on startup to initialize your routing table.
- Call `subscribe_membership_events()` to listen for `MEMBER_JOINED` (add to fanout) and `LEAVE_CONFIRMED` / `DISCONNECT_TIMEOUT` (remove from fanout).
- **Important:** Only route messages to members in the `ACTIVE` state. Do not route to members who are still `BACKFILLING`.

### Message History Team

- Subscribe to events to detect when a member joins (`JOIN_ACCEPTED`).
- Call `start_history_backfill(user_id)` before you begin replaying message history to the new member.
- Once replay is done, call `complete_history_backfill(user_id)`. The member will then transition to `ACTIVE` and be ready to receive live messages.

### Security Team

- Register a join validator hook (if supported by the service instance) to approve or reject join requests based on authorization or bans.
- Subscribe to events for audit logging (e.g., capturing `JOIN_ACCEPTED` and `LEAVE_CONFIRMED`).
- Call `leave_member(user_id)` to forcibly eject a user when necessary.

---

## Core Guarantees

| Guarantee | Reality |
|---|---|
| Single Source of Truth | Yes — The `MembershipEventLog` acts as the unquestionable append-only ledger of state changes. |
| Deterministic State | Yes — The `MembershipSnapshot` is a materialized view derived strictly by applying the event log in order. |
| Event Notifications | Yes — Subscribers are notified sequentially of valid membership transitions via `EventNotifier`. |
| Fast Local Reads | Yes — `get_membership_snapshot()` provides an O(1) in-memory lookup. |
| Resilience to Stale Reads | Yes — Version numbers are attached to snapshots and updates to guard against out-of-order processing. |

---

## Design Decisions

| Decision | Rationale |
|---|---|
| **Control-Plane Focus** | The membership service isolates complex peer state (alive, suspected, backfilling) from high-throughput data-plane tasks (message routing). |
| **Append-Only Log** | Modeled after Kafka/Raft. Makes it trivial to reconstruct state, build new materialized views, and debug past issues by replaying events. |
| **Suspect-Before-Dead (SWIM)** | Traditional heartbeat timeouts cause churn. Adding a `SUSPECTED` grace period reduces false positives for peers experiencing transient GC pauses or network blips. |
| **Dapper-Style Tracing** | Attaching `trace_id`s to the join lifecycle (which spans multiple teams) makes it possible to debug exactly where a join request stalled. |
| **ZooKeeper-Style Watches** | Consumers register callbacks for events instead of polling `get_membership_snapshot()`, reducing unnecessary overhead and keeping systems in sync. |

---

## Known Limitations

- **Centralized Coordinator:** In its current phase, the `MembershipCoordinator` handles all mutations, making it a single point of failure. Phase 5 plans address this via Raft-style leader election.
- **In-Memory State:** Large room sizes could bloat the `MembershipSnapshot`. State persistence relies on `durability.py` snapshots.

---

## Team

| Member | Contribution |
|---|---|
| Person A | Event Log and Snapshot functionality |
| Person B | Durability, Idempotency, and core models |
| Person C | Coordinator, Tracing, Notifier, and public Service Facade |
| Person D | External Integration (Message Distribution, History, Security) |
