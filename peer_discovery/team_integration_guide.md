# Membership & Presence Service — Team Integration Guide

**Audience:** Message History, Message Distribution, and Security teams
**Purpose:** Everything you need to integrate with the Membership & Presence Service. Nothing you don't.

---

## How to Read This Document

This guide is split into four parts. **Read only your team's section.** Each section is self-contained and tells you exactly which methods to call, what data you'll receive, what you're responsible for, and what you should never do.

- **Part 1:** Overview (everyone reads this — it's short)
- **Part 2:** Message History Team (includes Recovery feature)
- **Part 3:** Message Distribution Team
- **Part 4:** Security Team

---

## Part 1: Overview (All Teams)

### What the Membership Service Is

The Membership Service is the single authority on **who is in the chat room**. It tracks joins, leaves, disconnects, and reconnections. It manages the full lifecycle of a member from the moment they request to join until they leave or get disconnected.

You do not need to know how it works internally. You interact with it through a Python API of seven methods. Most teams only need two or three of them.

### What the Membership Service Is NOT

It does not store or deliver chat messages. It does not handle message fanout. It does not store message history. It is a control-plane service — it tells you *who* is in the room so your system can decide *what* to do with that information.

### The API at a Glance

```python
from membership_service import MembershipService

service = MembershipService()

# Mutating operations (used by client-facing code and internal lifecycle)
service.join_member(user_id, display_name) -> JoinResult
service.leave_member(user_id) -> None
service.heartbeat_member(user_id) -> None

# Read operations
service.get_membership_snapshot() -> MembershipSnapshot

# Subscription
service.subscribe_membership_events(callback) -> SubscriptionHandle

# History team handoff
service.start_history_backfill(user_id) -> None
service.complete_history_backfill(user_id) -> None
```

### Member States

Every member in the room is in exactly one of these states:

| State | Meaning |
|-------|---------|
| `JOINING` | Join accepted, waiting for backfill to begin |
| `BACKFILLING` | History team is replaying past messages to this member |
| `ACTIVE` | Fully caught up, participating in the room |
| `SUSPECTED` | Missed heartbeats, may be disconnected (grace period) |
| `DISCONNECTED` | Confirmed disconnected after grace period expired |
| `LEAVING` | Voluntary leave in progress |
| `LEFT` | Clean departure complete |

### The MemberInfo Object

Every method that returns member data gives you `MemberInfo` objects:

```python
@dataclass
class MemberInfo:
    user_id: str          # Unique identifier
    display_name: str     # Human-readable name
    state: MemberState    # One of the states listed above
    joined_at: float      # Unix timestamp of when they joined
    last_heartbeat: float # Unix timestamp of last liveness signal
    membership_version: int  # Increments on every state change
```

### The MembershipSnapshot Object

```python
@dataclass
class MembershipSnapshot:
    room_id: str
    version: int                    # Current global version number
    members: dict[str, MemberInfo]  # user_id -> MemberInfo
    active_count: int               # Count of members in ACTIVE state
    as_of_seq_no: int               # Log position this snapshot reflects
```

### The MembershipEvent Object

If you subscribe to events, your callback receives these:

```python
@dataclass
class MembershipEvent:
    seq_no: int             # Position in the event log (monotonically increasing)
    room_id: str
    user_id: str            # Which member this event is about
    event_type: EventType   # What happened (see below)
    timestamp: float        # When it happened
    membership_version: int # Snapshot version after this event
    source: str             # Which component produced this event
    trace_id: str | None    # Optional tracing correlation ID
```

### Event Types

```python
class EventType(Enum):
    JOIN_REQUESTED        = "JOIN_REQUESTED"
    JOIN_ACCEPTED         = "JOIN_ACCEPTED"
    JOIN_REJECTED         = "JOIN_REJECTED"
    LEAVE_REQUESTED       = "LEAVE_REQUESTED"
    LEAVE_CONFIRMED       = "LEAVE_CONFIRMED"
    HEARTBEAT             = "HEARTBEAT"
    DISCONNECT_SUSPECTED  = "DISCONNECT_SUSPECTED"
    DISCONNECT_TIMEOUT    = "DISCONNECT_TIMEOUT"
    RECONNECTED           = "RECONNECTED"
    HISTORY_BACKFILL_STARTED  = "HISTORY_BACKFILL_STARTED"
    HISTORY_BACKFILL_COMPLETE = "HISTORY_BACKFILL_COMPLETE"
```

### The Golden Rule

**You never write membership state.** Only the Membership Service's internal coordinator writes to the membership log. You read snapshots, subscribe to events, and call the provided API methods. If you find yourself maintaining your own copy of "who is in the room," stop — use `get_membership_snapshot()` instead.

---

## Part 2: Message History Team

### Your Relationship with Membership

You own message storage and replay. The Membership Service depends on you for one critical operation: **backfilling new members with recent chat history** when they join the room. You also own a **Recovery** feature that reconstructs state after crashes, and the Membership Service's event log and snapshots are your input for that.

### Methods You Use

You interact with exactly **three** methods and **one** subscription:

```python
# 1. Subscribe to know when a new member needs backfill
handle = service.subscribe_membership_events(your_callback)

# 2. Tell Membership that you've started replaying history
service.start_history_backfill(user_id)

# 3. Tell Membership that replay is done
service.complete_history_backfill(user_id)
```

You also use `get_membership_snapshot()` for the Recovery feature (details below).

### The Backfill Protocol Step by Step

This is the core handoff between Membership and your team. Here is the exact sequence of what happens when a user joins:

```
Step  Who Does It              What Happens
────  ───────────────────────  ──────────────────────────────────────────
 1    Client code              Calls service.join_member("alice", "Alice")
 2    Membership coordinator   Appends JOIN_REQUESTED to the event log
 3    Membership coordinator   Appends JOIN_ACCEPTED to the event log
 4    Membership coordinator   Member state is now JOINING
 5    Membership coordinator   Fires event to all subscribers
 6    YOUR CALLBACK            Receives the JOIN_ACCEPTED event
 7    YOUR CODE                Calls service.start_history_backfill("alice")
 8    Membership coordinator   Appends HISTORY_BACKFILL_STARTED
 9    Membership coordinator   Member state is now BACKFILLING
10    YOUR CODE                Replays recent messages to alice
11    YOUR CODE                Calls service.complete_history_backfill("alice")
12    Membership coordinator   Appends HISTORY_BACKFILL_COMPLETE
13    Membership coordinator   Member state is now ACTIVE
14    Membership coordinator   Fires event to all subscribers
```

### Your Callback Implementation

```python
def on_membership_event(event: MembershipEvent, delta: MembershipDelta):
    """
    This is what you register with subscribe_membership_events().
    You only care about JOIN_ACCEPTED events.
    """
    if event.event_type == EventType.JOIN_ACCEPTED:
        # A new member needs history backfill
        start_backfill_for_user(
            user_id=event.user_id,
            room_id=event.room_id,
            joined_at_seq_no=event.seq_no,  # Useful for knowing "backfill up to here"
        )


def start_backfill_for_user(user_id: str, room_id: str, joined_at_seq_no: int):
    """
    Your backfill logic. This is YOUR code, not ours.
    """
    # Step 1: Tell Membership that backfill has begun
    service.start_history_backfill(user_id)

    # Step 2: Replay recent messages to this user
    # (Your implementation — fetch from your message store,
    #  send to the user's connection, etc.)
    recent_messages = your_message_store.get_recent(room_id, limit=100)
    for msg in recent_messages:
        send_to_user(user_id, msg)

    # Step 3: Tell Membership that backfill is complete
    service.complete_history_backfill(user_id)
```

### What Happens If Your Backfill Takes Too Long

The Membership Service has a configurable **backfill timeout** (default: 30 seconds). If you don't call `complete_history_backfill()` within that window, the coordinator will automatically mark the member as `DISCONNECTED`. The user would then need to rejoin.

**What this means for you:** Make sure your replay completes within the timeout. If you expect replay to take longer (e.g., very large history), coordinate with the Membership team to increase the threshold.

### What Happens If Your Backfill Fails

If your replay crashes partway through, just don't call `complete_history_backfill()`. The timeout mechanism will handle cleanup. The member stays in `BACKFILLING` state until the timeout fires, then transitions to `DISCONNECTED`. They can rejoin and the backfill will be retried from scratch.

You do NOT need to call any cleanup or error-reporting method on the Membership Service. The timeout is your safety net.

### Events You Should Listen For (Beyond Backfill)

For a more complete integration, you may also want to listen for these events:

| Event | Why You Might Care |
|-------|-------------------|
| `LEAVE_CONFIRMED` | Stop sending messages to this user |
| `DISCONNECT_TIMEOUT` | Stop sending messages to this user |
| `RECONNECTED` | A suspected user came back — may need a mini-backfill of messages they missed during the suspicion window |

### Recovery Feature Integration

Your Recovery feature needs to reconstruct the current room state after a crash. The Membership Service supports this through its **snapshot + replay** model.

**How Recovery works from your perspective:**

```python
def recover_room_state(room_id: str):
    """
    Called by your Recovery feature after a crash or restart.
    """
    # Step 1: Get the current membership snapshot
    # This gives you the exact current state of who is in the room,
    # what state they're in, and what version you're at.
    snapshot = service.get_membership_snapshot()

    # Step 2: Use the snapshot to rebuild your internal routing
    for user_id, member in snapshot.members.items():
        if member.state == MemberState.ACTIVE:
            # This member is fully caught up — resume normal delivery
            your_router.add_active_member(user_id)

        elif member.state == MemberState.BACKFILLING:
            # This member was mid-backfill when the crash happened.
            # You need to restart their backfill from scratch.
            start_backfill_for_user(
                user_id=user_id,
                room_id=room_id,
                joined_at_seq_no=snapshot.as_of_seq_no,
            )

        elif member.state == MemberState.SUSPECTED:
            # Member might be alive, might not. Add them tentatively.
            your_router.add_tentative_member(user_id)

        # Members in DISCONNECTED, LEFT, LEAVING states:
        # Don't route messages to them.

    # Step 3: Subscribe to events going forward
    # Use the snapshot's version so you don't miss events
    # that occurred between snapshot read and subscription start.
    handle = service.subscribe_membership_events(
        your_callback,
        from_version=snapshot.version
    )

    return snapshot
```

**Key detail:** The `from_version` parameter on `subscribe_membership_events()` tells the Membership Service to first deliver any events that occurred between that version and the current version (a catch-up batch), and then switch to live streaming. This eliminates the gap between reading the snapshot and starting the subscription.

**What the snapshot's `as_of_seq_no` means for you:** This is the position in the membership event log that the snapshot reflects. If you store this value before a crash, you can use it after recovery to know exactly which membership events you've already processed. Any events with `seq_no > as_of_seq_no` are new to you.

### Your Complete Integration Checklist

1. ☐ Register a callback with `subscribe_membership_events()`
2. ☐ In the callback, handle `JOIN_ACCEPTED` by starting backfill
3. ☐ Call `start_history_backfill(user_id)` before beginning replay
4. ☐ Call `complete_history_backfill(user_id)` after replay finishes
5. ☐ Ensure backfill completes within the timeout window (default 30s)
6. ☐ Handle `LEAVE_CONFIRMED` and `DISCONNECT_TIMEOUT` to stop delivery
7. ☐ For Recovery: call `get_membership_snapshot()` on startup
8. ☐ For Recovery: re-subscribe with `from_version` to avoid gaps
9. ☐ For Recovery: restart backfill for any members stuck in `BACKFILLING`

### What You Must Never Do

- Never maintain your own "who is in the room" state independent of the Membership snapshot. Use `get_membership_snapshot()`.
- Never call `join_member()` or `leave_member()`. Those are not your methods.
- Never assume a user is `ACTIVE` just because you received `JOIN_ACCEPTED`. They're `ACTIVE` only after you complete backfill and the `HISTORY_BACKFILL_COMPLETE` event fires.

---

## Part 3: Message Distribution Team

### Your Relationship with Membership

You own message fanout — when someone sends a chat message, you deliver it to all active members in the room. You need to know who is currently in the room and who to route messages to. The Membership Service tells you this.

### Methods You Use

You interact with exactly **two** methods:

```python
# 1. Get the current room roster on startup
snapshot = service.get_membership_snapshot()

# 2. Subscribe to roster changes in real time
handle = service.subscribe_membership_events(your_callback)
```

That's it. You never call `join_member()`, `leave_member()`, `heartbeat_member()`, or any of the backfill methods. Those belong to other components.

### Initialization: Building Your Routing Table

When your message distribution service starts (or restarts), you need to know who to deliver messages to:

```python
def initialize_routing():
    """Called on startup to build your initial fanout list."""

    snapshot = service.get_membership_snapshot()

    routing_table = {}
    for user_id, member in snapshot.members.items():
        if member.state == MemberState.ACTIVE:
            # Deliver real-time messages to this member
            routing_table[user_id] = RoutingEntry(
                user_id=user_id,
                display_name=member.display_name,
                status="deliver",
            )
        elif member.state == MemberState.BACKFILLING:
            # Don't deliver real-time messages yet.
            # This member is still catching up on history.
            # They'll become ACTIVE once backfill completes.
            routing_table[user_id] = RoutingEntry(
                user_id=user_id,
                display_name=member.display_name,
                status="hold",  # Queue messages, deliver after ACTIVE
            )
        elif member.state == MemberState.SUSPECTED:
            # Member might be alive. You can choose to:
            # Option A: Keep delivering (they might come back)
            # Option B: Buffer messages (deliver on reconnect)
            routing_table[user_id] = RoutingEntry(
                user_id=user_id,
                display_name=member.display_name,
                status="buffer",
            )
        # DISCONNECTED, LEFT, LEAVING: don't add to routing table

    # Subscribe to changes going forward, using snapshot version
    # to avoid missing events between snapshot and subscription
    handle = service.subscribe_membership_events(
        on_membership_change,
        from_version=snapshot.version
    )

    return routing_table
```

### Your Callback: Handling Roster Changes

```python
def on_membership_change(event: MembershipEvent, delta: MembershipDelta):
    """
    Called every time the room roster changes.
    Update your routing table accordingly.
    """

    match event.event_type:

        case EventType.JOIN_ACCEPTED:
            # New member joining, but they're not ACTIVE yet.
            # They're in JOINING/BACKFILLING state.
            # Don't deliver real-time messages to them yet.
            routing_table[event.user_id] = RoutingEntry(
                user_id=event.user_id,
                status="hold",
            )

        case EventType.HISTORY_BACKFILL_COMPLETE:
            # Member is now fully caught up. Start delivering.
            if event.user_id in routing_table:
                routing_table[event.user_id].status = "deliver"

        case EventType.LEAVE_CONFIRMED:
            # Member left voluntarily. Stop delivering.
            routing_table.pop(event.user_id, None)

        case EventType.DISCONNECT_SUSPECTED:
            # Member might be disconnected. Your choice:
            # buffer their messages or keep trying to deliver.
            if event.user_id in routing_table:
                routing_table[event.user_id].status = "buffer"

        case EventType.DISCONNECT_TIMEOUT:
            # Confirmed disconnected. Stop delivering.
            routing_table.pop(event.user_id, None)

        case EventType.RECONNECTED:
            # Member came back from SUSPECTED state.
            # Resume delivery. Flush any buffered messages.
            if event.user_id in routing_table:
                routing_table[event.user_id].status = "deliver"
                flush_buffered_messages(event.user_id)
```

### Events That Matter to You

| Event | Your Action |
|-------|-------------|
| `JOIN_ACCEPTED` | Add to routing table with `hold` status (don't deliver yet) |
| `HISTORY_BACKFILL_COMPLETE` | Switch to `deliver` status |
| `LEAVE_CONFIRMED` | Remove from routing table |
| `DISCONNECT_SUSPECTED` | Optionally buffer messages instead of delivering |
| `DISCONNECT_TIMEOUT` | Remove from routing table |
| `RECONNECTED` | Resume delivery, flush any buffered messages |

### Events You Can Ignore

| Event | Why You Can Ignore It |
|-------|-----------------------|
| `JOIN_REQUESTED` | Nothing actionable yet — wait for `JOIN_ACCEPTED` |
| `JOIN_REJECTED` | Member never entered the room |
| `LEAVE_REQUESTED` | Leave isn't confirmed yet — keep delivering until `LEAVE_CONFIRMED` |
| `HEARTBEAT` | Internal liveness tracking, not relevant to message routing |
| `HISTORY_BACKFILL_STARTED` | You already have the member on `hold` from `JOIN_ACCEPTED` |

### The BACKFILLING / ACTIVE Distinction

This is the most important thing to understand: **do not deliver real-time messages to members in BACKFILLING state.**

Why? Because the History team is replaying older messages to them. If you also deliver new real-time messages simultaneously, the user sees messages out of order (old messages from the history replay mixed with new messages from your fanout). Wait until `HISTORY_BACKFILL_COMPLETE` fires, then start delivering.

If you want to buffer real-time messages that arrive during backfill and deliver them immediately after the member goes `ACTIVE`, that's a valid optimization. The `HISTORY_BACKFILL_COMPLETE` event is your signal to flush that buffer.

### Handling the SUSPECTED State

When a member enters `SUSPECTED` state, it means they've missed several heartbeats but haven't been confirmed dead yet. They might come back within a few seconds. You have two options:

**Option A — Keep delivering:** Continue sending messages to them. If their connection is actually dead, the messages will fail to deliver, but that's harmless. If they come back, they won't have missed anything.

**Option B — Buffer:** Stop delivering, queue messages locally. When `RECONNECTED` fires, flush the buffer. If `DISCONNECT_TIMEOUT` fires instead, discard the buffer.

Option A is simpler. Option B is more efficient for your network layer. Choose based on your architecture.

### Restart / Recovery

After a restart, call `get_membership_snapshot()` to get the current roster, then subscribe with `from_version=snapshot.version` so you don't miss any events between the snapshot and your subscription:

```python
def on_restart():
    snapshot = service.get_membership_snapshot()
    rebuild_routing_table_from(snapshot)
    handle = service.subscribe_membership_events(
        on_membership_change,
        from_version=snapshot.version
    )
```

This is identical to the initialization flow. There is no special recovery protocol for your team — the snapshot gives you a consistent view of the current membership at any point in time.

### Your Complete Integration Checklist

1. ☐ On startup, call `get_membership_snapshot()` to build initial routing table
2. ☐ Subscribe to events with `from_version` to avoid gaps
3. ☐ Handle `JOIN_ACCEPTED` → add member on `hold`
4. ☐ Handle `HISTORY_BACKFILL_COMPLETE` → switch to `deliver`
5. ☐ Handle `LEAVE_CONFIRMED` → remove from routing table
6. ☐ Handle `DISCONNECT_TIMEOUT` → remove from routing table
7. ☐ Handle `DISCONNECT_SUSPECTED` → decide on buffer vs continue
8. ☐ Handle `RECONNECTED` → resume delivery
9. ☐ Never deliver real-time messages to members in `BACKFILLING` state

### What You Must Never Do

- Never call `join_member()`, `leave_member()`, or `heartbeat_member()`. Those are not your methods.
- Never call the backfill methods. Those are for the History team.
- Never maintain an independent "who is online" list that diverges from the Membership snapshot. If your routing table disagrees with the snapshot, the snapshot is correct.
- Never assume a member is ready for messages on `JOIN_ACCEPTED`. Wait for `HISTORY_BACKFILL_COMPLETE`.

---

## Part 4: Security Team

### Your Relationship with Membership

You own authentication, authorization, and audit. The Membership Service needs your team for **join validation** (is this user allowed to enter this room?) and you need the Membership Service for **audit logging** (who joined, who left, when, from where).

### Integration Point 1: Join Validation Hook

Before the Membership Service accepts a join request, it can call a validation function that you provide. This is where you enforce access control.

```python
# You provide this function. Membership calls it during join_member().
def validate_join(user_id: str, room_id: str, context: dict) -> ValidationResult:
    """
    Called by the Membership coordinator before appending JOIN_ACCEPTED.
    Return accepted=True to allow the join, or accepted=False with a reason.

    Parameters:
        user_id:  The user attempting to join
        room_id:  The room they're trying to join
        context:  Additional metadata (IP address, session token, etc.)

    Returns:
        ValidationResult with accepted (bool) and reason (str, optional)
    """
    # Example: Check if user is banned
    if is_banned(user_id, room_id):
        return ValidationResult(accepted=False, reason="user_banned")

    # Example: Check if room is at capacity
    if is_room_full(room_id):
        return ValidationResult(accepted=False, reason="room_full")

    # Example: Check if user has permission for this room
    if not has_room_access(user_id, room_id):
        return ValidationResult(accepted=False, reason="no_access")

    return ValidationResult(accepted=True)
```

```python
@dataclass
class ValidationResult:
    accepted: bool
    reason: str | None = None  # Only needed if accepted=False
```

**How to register your validator:**

```python
# During system initialization
service.register_join_validator(your_validate_join_function)
```

When validation fails, the Membership Service appends a `JOIN_REJECTED` event to the log (which includes your reason string) and returns `JoinResult(accepted=False)` to the caller. The user never enters the room.

**Important:** Your validation function is called synchronously during the join flow. It must be fast — aim for under 50ms. If you need to do slow lookups (e.g., hitting an external authorization service), cache aggressively or pre-warm the cache on room load.

### Integration Point 2: Audit Event Subscription

You subscribe to membership events to maintain a security audit trail:

```python
handle = service.subscribe_membership_events(on_security_event)

def on_security_event(event: MembershipEvent, delta: MembershipDelta):
    """
    Log security-relevant membership events to your audit system.
    """
    # Events you care about for audit purposes:
    audit_relevant = {
        EventType.JOIN_ACCEPTED,
        EventType.JOIN_REJECTED,
        EventType.LEAVE_CONFIRMED,
        EventType.DISCONNECT_TIMEOUT,
        EventType.RECONNECTED,
    }

    if event.event_type in audit_relevant:
        your_audit_log.record(
            event_type=event.event_type.value,
            user_id=event.user_id,
            room_id=event.room_id,
            timestamp=event.timestamp,
            seq_no=event.seq_no,
            source=event.source,
            trace_id=event.trace_id,  # Correlate with Dapper-style traces if needed
        )
```

### Events That Matter to You

| Event | Security Relevance |
|-------|-------------------|
| `JOIN_ACCEPTED` | Who entered the room and when. Log for audit. |
| `JOIN_REJECTED` | Failed join attempt. Log with the reason from your validator. |
| `LEAVE_CONFIRMED` | Who left and when. Log for audit. |
| `DISCONNECT_TIMEOUT` | Unclean departure — could indicate a kicked/crashed user. |
| `RECONNECTED` | User recovered from suspected state — might indicate flaky connection or session hijack attempt worth flagging. |

### Events You Can Likely Ignore

| Event | Why |
|-------|-----|
| `JOIN_REQUESTED` | Precedes validation — not yet meaningful for audit |
| `LEAVE_REQUESTED` | Leave not confirmed yet |
| `HEARTBEAT` | Too high volume for audit logging, no security signal |
| `DISCONNECT_SUSPECTED` | Intermediate state — wait for `DISCONNECT_TIMEOUT` or `RECONNECTED` for the final outcome |
| `HISTORY_BACKFILL_STARTED` | Internal lifecycle, no security relevance |
| `HISTORY_BACKFILL_COMPLETE` | Internal lifecycle, no security relevance |

### Integration Point 3: Forced Removal

If your security system detects that a user should be ejected from the room (e.g., banned mid-session, reported for abuse), you can call the standard leave method:

```python
# Force-remove a user from the room
service.leave_member(user_id)
```

This triggers the normal leave flow (`LEAVE_REQUESTED` → `LEAVE_CONFIRMED`), removes them from the snapshot, and notifies all subscribers. The user's client will receive the leave event through whatever connection layer you have.

**Note:** This is the same method used for voluntary leaves. The `source` field on the resulting event will indicate it came from the security component, so your audit log can distinguish between voluntary leaves and forced removals.

### Integration Point 4: Snapshot for Access Reviews

For periodic access reviews or compliance checks, you can pull the full room roster:

```python
def run_access_review(room_id: str):
    """
    Periodic check: is everyone in this room still authorized to be here?
    """
    snapshot = service.get_membership_snapshot()

    for user_id, member in snapshot.members.items():
        if member.state in (MemberState.ACTIVE, MemberState.BACKFILLING,
                            MemberState.SUSPECTED):
            if not has_room_access(user_id, room_id):
                # User's access was revoked while they were in the room
                service.leave_member(user_id)
                your_audit_log.record(
                    event_type="forced_removal_access_revoked",
                    user_id=user_id,
                    room_id=room_id,
                )
```

### Trace ID Correlation

Every membership event optionally carries a `trace_id` field. This is a Dapper-style correlation ID that tracks the full join lifecycle across components. If you receive a `JOIN_ACCEPTED` event with a `trace_id`, you can use that same ID to correlate your audit log entry with:

- The History team's backfill timing for that user
- The Message Distribution team's routing setup for that user
- Any latency or error investigations across the full join path

You don't need to generate trace IDs — the Membership Service creates them automatically for join flows.

### Your Complete Integration Checklist

1. ☐ Implement `validate_join()` and register it with `register_join_validator()`
2. ☐ Ensure your validator responds in under 50ms
3. ☐ Subscribe to membership events for audit logging
4. ☐ Log `JOIN_ACCEPTED`, `JOIN_REJECTED`, `LEAVE_CONFIRMED`, `DISCONNECT_TIMEOUT`, `RECONNECTED`
5. ☐ Use `leave_member()` for forced removals (ban, abuse, access revoked)
6. ☐ Optionally run periodic access reviews using `get_membership_snapshot()`
7. ☐ Use `trace_id` for cross-component correlation in investigations

### What You Must Never Do

- Never directly modify the membership event log or snapshot. Use the API methods.
- Never implement your own "is user in room" check by bypassing the Membership Service. Use `get_membership_snapshot()`.
- Never block the `validate_join()` callback for more than a few hundred milliseconds. The entire join flow waits on your response.

---

## Appendix A: Quick Reference — Which Methods Does My Team Use?

| Method | History | Distribution | Security |
|--------|---------|-------------|----------|
| `join_member()` | ✗ | ✗ | ✗ (but see forced removal note) |
| `leave_member()` | ✗ | ✗ | ✓ (forced removal) |
| `heartbeat_member()` | ✗ | ✗ | ✗ |
| `get_membership_snapshot()` | ✓ (recovery) | ✓ (init + restart) | ✓ (access reviews) |
| `subscribe_membership_events()` | ✓ | ✓ | ✓ |
| `start_history_backfill()` | ✓ | ✗ | ✗ |
| `complete_history_backfill()` | ✓ | ✗ | ✗ |
| `register_join_validator()` | ✗ | ✗ | ✓ |

## Appendix B: Quick Reference — Which Events Does My Team Handle?

| Event | History | Distribution | Security |
|-------|---------|-------------|----------|
| `JOIN_REQUESTED` | ignore | ignore | ignore |
| `JOIN_ACCEPTED` | **handle** (start backfill) | **handle** (add on hold) | **audit** |
| `JOIN_REJECTED` | ignore | ignore | **audit** |
| `LEAVE_REQUESTED` | ignore | ignore | ignore |
| `LEAVE_CONFIRMED` | optional (stop delivery) | **handle** (remove) | **audit** |
| `HEARTBEAT` | ignore | ignore | ignore |
| `DISCONNECT_SUSPECTED` | ignore | optional (buffer) | ignore |
| `DISCONNECT_TIMEOUT` | optional (stop delivery) | **handle** (remove) | **audit** |
| `RECONNECTED` | optional (mini-backfill) | **handle** (resume) | **audit** |
| `HISTORY_BACKFILL_STARTED` | ignore (you triggered it) | ignore | ignore |
| `HISTORY_BACKFILL_COMPLETE` | ignore (you triggered it) | **handle** (start deliver) | ignore |

## Appendix C: Error Handling Summary

| Scenario | What Happens | Your Responsibility |
|----------|-------------|-------------------|
| Your callback throws an exception | The Membership Service catches it and continues notifying other subscribers. Your callback is NOT retried automatically. | Wrap your callback in try/except. Log errors. Consider re-subscribing if your callback enters a bad state. |
| You call `start_history_backfill()` for an unknown user | The call is silently ignored. No event is appended. | Check that the user exists in the snapshot before calling. |
| You call `complete_history_backfill()` for a user not in `BACKFILLING` | The call is silently ignored. | Only call this after you've called `start_history_backfill()` for the same user. |
| The Membership Service restarts | Your subscription handle becomes invalid. You need to re-subscribe. | On detecting a disconnection, call `get_membership_snapshot()` and re-subscribe with `from_version`. |
| You call `leave_member()` for a user already in `LEFT` or `DISCONNECTED` | The call is silently ignored. No duplicate events. | Safe to call idempotently. |
| Backfill times out before you call `complete_history_backfill()` | Member is auto-transitioned to `DISCONNECTED`. A `DISCONNECT_TIMEOUT` event fires. | You'll receive the `DISCONNECT_TIMEOUT` event in your subscription. Clean up any in-progress backfill state. |

---

## Part 5: Network Team

### Your Relationship with Membership

You own peer discovery, network transport, and cryptographic identity. The Membership Service now supports distributed state synchronization through a Gossip protocol. You wrap the core `MembershipService` with the `DiscoveryNode` to form a distributed P2P network.

### P2P API Additions

The original API remains fully backward-compatible for local teams. For network integration, the following extensions are available:

- `MembershipEvent` and `MemberInfo` now include `public_key` (bytes) and `originator` (str) fields.
- `service.join_member()` accepts `public_key` and `context` kwargs, which are passed directly to the Security Team's `validate_join` hook.
- `service.apply_remote_snapshot(events)`: Reconstructs state from a bootstrap peer.
- `service.apply_remote_event(event)`: Idempotently applies a gossiped event from a remote peer.

### Network Architecture

The `DiscoveryNode` (`peer_discovery.network.discovery_node`) handles the following:

1. **Transport**: TCP sockets with length-prefixed framing (64KB max) and strict 30s read/write timeouts. Thread-per-connection model capped at 20 workers.
2. **Crypto**: Hybrid encryption (RSA-2048 OAEP for AES key exchange + AES-256-GCM for payload encryption).
3. **Bootstrap**: A joining node connects to a known peer, submits its RSA public key in a `JOIN_REQUEST`, and if accepted, receives the full event log encrypted with its public key.
4. **Gossip**: Local state changes are broadcast via `EVENT_BROADCAST`. Duplicate gossip is suppressed via a bounded LRU cache (`seen_event_ids`).
5. **Presence**: `HeartbeatManager` periodically pings all known peers. The existing `MembershipCoordinator.tick()` sweep translates missed heartbeats into `DISCONNECT_SUSPECTED` and `DISCONNECT_TIMEOUT` events, which are then gossiped to the network.
