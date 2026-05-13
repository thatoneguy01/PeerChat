# Integration Contract — Peer Discovery Team

**Owner on our side:** Bhuvana (Message Distribution POC)
**Audience:** Peer Discovery team POC
**Status:** REFRESHED for sign-off, 2026-05-12 — supersedes the initial draft
**File the MD team ships against:** `distribution/peer_registry.py`, `distribution/membership_router.py`

---

## Status

Integration is **already wired up**. `distribution/membership_router.py` consumes your `MembershipService` interface. This contract documents what we assume so you can confirm or flag drift before we ship.

## What Message Distribution consumes from you

We implement a `PeerRegistry` named `MembershipRouter` that your `MembershipService` feeds. Concretely, we use two methods and one event stream:

```python
service.get_membership_snapshot()
    # -> object with .members: dict[user_id, Member], and .version: int
    # Each Member has a .state with a .name attribute

service.subscribe_membership_events(callback, from_version=...)
    # -> subscription handle
    # callback(event, delta=None) is invoked from your event thread
    # event has .event_type (with .name) and .user_id
```

## Membership states we depend on

| State | Our behaviour |
|---|---|
| `ACTIVE` | Peer receives real-time broadcasts |
| `JOINING` / `BACKFILLING` | Peer is held (buffered); not sent broadcasts until backfill completes |
| `SUSPECTED` | Peer is held; resumes once `RECONNECTED` fires |
| `DISCONNECTED` / `LEFT` / `LEAVING` | Peer is skipped on every `get_peers()` call |

## Events we subscribe to

| Event | Our reaction |
|---|---|
| `JOIN_ACCEPTED` | Add user to hold-back (not yet ACTIVE) |
| `HISTORY_BACKFILL_COMPLETE` | Promote hold-back → ACTIVE |
| `DISCONNECT_SUSPECTED` | Demote ACTIVE → hold-back |
| `RECONNECTED` | Promote hold-back → ACTIVE |
| `LEAVE_CONFIRMED` | Remove from all routing |
| `DISCONNECT_TIMEOUT` | Remove from all routing |
| everything else (`HEARTBEAT`, `JOIN_REQUESTED`, `JOIN_REJECTED`, `LEAVE_REQUESTED`) | Ignored |

If any of those event names are going to change before tomorrow, tell us — grep `membership_router.py` to see where each one is matched.

## Contract guarantees we need

| Item | Expected behaviour |
|---|---|
| `user_id` format | `"host:port"` string (e.g., `"127.0.0.1:5001"`). We parse on `:` and take the last segment as port. If the format changes, we break. |
| Snapshot members dict | May include tombstoned members in `LEFT` / `DISCONNECTED` / `LEAVING` states — we skip them on the initialization scan and never add them to routing. The router is responsible for filtering, not the snapshot. |
| Event ordering | Events per `user_id` must be delivered in causal order (e.g., `JOIN_ACCEPTED` before `HISTORY_BACKFILL_COMPLETE`). Cross-user ordering doesn't matter. |
| `from_version` correctness | Passing `from_version=snapshot.version` in `subscribe_membership_events` must not miss events between snapshot and subscription. |
| Thread safety | Your callback may fire on your own thread; we lock internally, so concurrent callbacks are fine. |
| Idempotency | Re-delivering the same event is safe on our side (we check state before mutating). |

## What you can assume about us

- We call `get_peers()` on every broadcast. Must be O(active peers). Current implementation: `list(self._active.values())` under a lock.
- We **never** mutate the returned list or any `Member`.
- We drop peers that fail 3 WS connection attempts (0.5s / 1.0s / 1.5s backoff). Your state machine doesn't need to track this — we handle it internally and emit a warning log.
- `InMemoryRegistry` still exists for our tests; it's not a replacement for you.

## What happens if you break the contract

- **Wrong `user_id` format** → we silently skip the peer with a log warning. No crash, but peer won't receive messages.
- **Unknown event name** → silently ignored. No crash, but state transitions that should have happened won't.
- **Snapshot–subscription gap** → peer that joined during the window is invisible to us until the next restart or state change.

## Open questions we need you to confirm by EOD 2026-05-12

- **Q1:** Is the event-name schema final, or is any rename pending? If any of the 6 we handle (`JOIN_ACCEPTED`, `HISTORY_BACKFILL_COMPLETE`, `DISCONNECT_SUSPECTED`, `RECONNECTED`, `LEAVE_CONFIRMED`, `DISCONNECT_TIMEOUT`) will change, tell us now.
- **Q2:** Is `user_id` always `"host:port"`? If multi-room support lands, does `user_id` become `"room:host:port"` or similar?
- **Q3:** Does `get_membership_snapshot()` block, and if so what's the worst case? We call it once at `MembershipRouter.__init__`; slow is fine once, fatal on every `get_peers()`.
