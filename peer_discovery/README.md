# Peer Discovery & Membership Service

Welcome to the **Single-Room Membership & Presence Service** for PeerChat. This module is responsible for managing the peer discovery and membership state of the chat room.

## Overview

The peer discovery service acts as the central control plane for room membership. It follows a log-centric architecture inspired by foundational distributed systems concepts (e.g., Raft, ZooKeeper, Kafka).

The core philosophy is **complex coordination hidden behind a small API**. This service acts as the single source of truth for:
- Who is currently in the room.
- Whether members are actively connected or suspected of being disconnected.
- Where members are in their join lifecycle (e.g., waiting for message history backfill).

## Architecture

The system is built in three primary layers:
1. **Event Log (Layer 1)**: An append-only sequence of `MembershipEvent` records. It acts as the undeniable source of truth.
2. **Materialized Snapshot (Layer 2)**: An in-memory projection of the current state, updated synchronously on every log append. This provides fast, O(1) lookups for consumers.
3. **Coordinator (Layer 3)**: The authoritative writer. All mutations (join, leave, heartbeat) flow through the Coordinator to ensure strict serialization and prevent conflicts.

Additional sub-components include:
- **Presence Manager**: Handles heartbeat tracking and uses a suspect-before-dead failure detection mechanism (inspired by SWIM).
- **Event Notifier**: Provides ZooKeeper-style watch subscriptions for other teams to react to membership changes.

For more deep-dive architectural details, refer to the documents in the `docs/` folder:
- `docs/OverallArchitechture.md`
- `docs/workstream_d_external_integration.md`

## How to Use the Service

Other teams (like Message Distribution, Message History, and Security) interact with this service exclusively through the `MembershipService` facade class. **You should never mutate the membership state directly.**

### The Public API

The `MembershipService` provides a clean, 7-method interface:

```python
class MembershipService:
    # 1. Join a member
    def join_member(self, user_id: str, display_name: str) -> JoinResult: ...

    # 2. Leave a member voluntarily
    def leave_member(self, user_id: str) -> None: ...

    # 3. Send a liveness heartbeat
    def heartbeat_member(self, user_id: str) -> None: ...

    # 4. Get a fast read of current state
    def get_membership_snapshot(self) -> MembershipSnapshot: ...

    # 5. Subscribe to membership events
    def subscribe_membership_events(self, callback: Callable) -> SubscriptionHandle: ...

    # 6. Signal backfill start (used by History Team)
    def start_history_backfill(self, user_id: str) -> None: ...

    # 7. Signal backfill complete (used by History Team)
    def complete_history_backfill(self, user_id: str) -> None: ...
```

### Team Integration Scenarios

#### Message Distribution Team
- Call `get_membership_snapshot()` on startup to initialize your routing table.
- Call `subscribe_membership_events()` to listen for `MEMBER_JOINED` (add to fanout) and `LEAVE_CONFIRMED` / `DISCONNECT_TIMEOUT` (remove from fanout).
- **Important:** Only route messages to members in the `ACTIVE` state. Do not route to members who are still `BACKFILLING`.

#### Message History Team
- Subscribe to events to detect when a member joins (`JOIN_ACCEPTED`).
- Call `start_history_backfill(user_id)` before you begin replaying message history to the new member.
- Once replay is done, call `complete_history_backfill(user_id)`. The member will then transition to `ACTIVE` and be ready to receive live messages.

#### Security Team
- Register a join validator hook (if supported by the service instance) to approve or reject join requests based on authorization or bans.
- Subscribe to events for audit logging (e.g., capturing `JOIN_ACCEPTED` and `LEAVE_CONFIRMED`).
- Call `leave_member(user_id)` to forcibly eject a user when necessary.

## Member Lifecycle

A member goes through several states during their time in the room:
`JOINING` -> `BACKFILLING` -> `ACTIVE` -> `LEAVING` -> `LEFT`

If an active member fails to send heartbeats, they enter a `SUSPECTED` state (a grace period). If they recover in time by sending a heartbeat, they return to `ACTIVE` without disruption. If they do not recover before the grace period ends, they become `DISCONNECTED`.
