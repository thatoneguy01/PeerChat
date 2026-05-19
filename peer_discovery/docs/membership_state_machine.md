# Membership State Machine

**Component:** Peer Discovery
**Authors:** Himanshu (event log, snapshot), Ali (durability, idempotency, models), Abhishek (coordinator, notifier, service facade)
**Status:** Current as of 2026-05-18

---

## 1. The Problem

A peer-to-peer chat room has no central database to ask "who is in this room?" Every peer needs to maintain the same answer locally, in real time, across joins, leaves, and unannounced crashes — and the answer has to stay consistent even when gossip reorders or duplicates events.

The membership state machine is the answer. It maintains, per room, a deterministic projection of all membership transitions: who is currently a member, what state they're in, what their public key is, and when we last heard from them.

The hard parts:

- The same JOIN_ACCEPTED event can arrive twice over gossip (cycle, retry, catch-up). Applying it twice corrupts state.
- Events can arrive out of order — a HISTORY_BACKFILL_COMPLETE before the JOIN_ACCEPTED that started the backfill, for example.
- Failures are not announced. A peer that vanished mid-session needs to be moved out of the active set on a timer, not a message.
- Restart has to be cheap. A node returning after a crash must catch up without replaying every event from genesis.

---

## 2. The State Machine

Every member moves through a small set of states. Transitions are gated by `_ALLOWED_FROM_STATES` in `peer_discovery/membership/snapshot.py:20-30` — an event whose "from" state isn't permitted raises `InvalidTransitionError` instead of silently corrupting state.

```text
                 JOIN_ACCEPTED
                       │
                       ▼
                   JOINING
                       │
                       │ HISTORY_BACKFILL_STARTED
                       ▼
                  BACKFILLING ──────────────────┐
                       │                        │ DISCONNECT_TIMEOUT
                       │ HISTORY_BACKFILL_      │ (backfill never completed)
                       │  COMPLETE              │
                       ▼                        ▼
                   ACTIVE ──────────────► DISCONNECTED
                    ▲ │
                    │ │ DISCONNECT_SUSPECTED
                    │ ▼
        RECONNECTED SUSPECTED ───────────► DISCONNECTED
                              DISCONNECT_TIMEOUT
                       │
                       │ LEAVE_REQUESTED
                       ▼
                   LEAVING
                       │
                       │ LEAVE_CONFIRMED
                       ▼
                    LEFT
```

`ACTIVE` is the only state from which Distribution will fan chat out — `MembershipRouter.get_peers()` returns ACTIVE only. `JOINING` and `BACKFILLING` are *held* states (the peer exists in the room but is not yet ready for chat). `SUSPECTED` is a presence-detector grace period, not a coordinator-driven state — see Section 5.

---

## 3. The Event Log

Source of truth: `peer_discovery/membership/event_log.py:9`. Append-only, single-writer, RLock-guarded. Every membership transition lives here as a `MembershipEvent` with a strictly monotonic `seq_no` per term.

The log is the only thing that ever needs to be durable. The snapshot is a pure projection; on restart it is rebuilt by replaying the log. This is the same property Kafka relies on — events are the source of truth, materialized views are caches.

A `MembershipEvent` (`models.py:31-44`) carries:

- `seq_no`, `room_id`, `user_id`, `event_type`, `timestamp`, `term`
- `membership_version` — version this event produced when applied
- `source` — `"local"` for events we originated, the originator's `user_id` for events arriving via gossip
- `originator` — only present on events that flowed in via gossip; needed for cross-node dedup
- `public_key` — PEM bytes, populated for JOIN_ACCEPTED so admission events double as pubkey distribution

Events serialize to and from JSON via `to_dict` / `from_dict` (`models.py:46-83`) so the same wire format is used for gossip payloads and durable checkpoints.

---

## 4. The Snapshot — Materialized View

`peer_discovery/membership/snapshot.py` holds, per `user_id`, a `MemberInfo` with the current state, display name, public key, and last-heartbeat timestamp. Snapshot reads are O(1). Materialize-from-cold is O(log size); a 100-event log replays in microseconds.

Two read paths that other layers actually use:

- `get_member(user_id)` — point lookup.
- `get_active_members()` — the only thing that drives Distribution's fan-out via `MembershipRouter`.

A `version` counter advances per applied event. Subscribers use it for subscribe-with-from-version semantics: catch up on missed events, then deliver live (`EventNotifier` in `peer_discovery/membership_integration/notifier.py`).

---

## 5. Presence Detection (SWIM-Style)

The presence layer (`peer_discovery/membership/presence.py:24`) is separate from the state machine on purpose. The state machine only changes state in response to an event in the log. Presence detection runs on a wall-clock timer: it watches heartbeats and decides when to *fire* an event that the coordinator then logs.

Two thresholds, set on the `PresenceManager`:

- **`suspect_after_missed = 3`** — if no heartbeat for `3 × heartbeat_interval` (15s default), the member moves from `ALIVE` to `SUSPECTED` and the manager calls `on_state_change(user_id, "SUSPECTED")`, which the coordinator turns into a `DISCONNECT_SUSPECTED` event.
- **`dead_after_suspect_s = 15.0`** — if a `SUSPECTED` peer has not heartbeated for another 15s, the manager fires `DISCONNECT_TIMEOUT`, which the coordinator turns into a logged event that drops the peer from the active set.

A heartbeat from a `SUSPECTED` peer immediately returns them to `ALIVE` and fires `RECONNECTED`. This is the two-phase part: a peer is given a chance to come back before being declared dead, but it can't sit in limbo forever.

The clock that makes this run is the `presence-tick` thread in `HeartbeatManager` (`peer_discovery/network/heartbeat.py:19`), which calls `MembershipService.tick()` once per `tick_interval` (1.0s default).

---

## 6. Idempotency and Duplicate Suppression

Gossip is intentionally redundant. The same JOIN_ACCEPTED can arrive from every peer who heard it. `DuplicateGuard` (`peer_discovery/membership/duplicate_guard.py`) makes that safe.

**Key shape:** `(originator, user_id, event_type, seq_no)`. The same shape is used by the gossip LRU on the wire and the durable guard in the log, so an event that has been applied can never be applied twice — even across restarts.

`source` is the discriminator between "this is the first time we're seeing this event" (`local` for events we originated, originator's user_id for events arriving via gossip) and the dedup lookup. Events with `source=="remote"` are not re-gossiped (see `peer_discovery/docs/network_layer.md` §5) — the originator gossips once, receivers apply and stop.

---

## 7. Durability and Recovery

`DurabilityManager` (`peer_discovery/membership/durability.py`) writes periodic snapshots so a restarting node doesn't have to replay the entire log from genesis. The snapshot artifact is a serialized `MembershipSnapshotData` plus the duplicate-guard's seen-event set.

Recovery path:

1. Load latest checkpoint into `MembershipSnapshot`.
2. Restore the dedup guard from the same checkpoint.
3. Replay any events after the checkpoint's `version` from the event log.
4. Start serving.

The dedup guard restoration is what keeps replay safe: gossip events arriving during the catch-up window can't double-apply, because the guard still knows we already saw them.

---

## 8. The History Backfill Handshake

A new joiner can't show chat history until it has been delivered the messages it missed. The handshake:

1. Joiner enters `JOINING` on JOIN_ACCEPTED.
2. Coordinator fires `HISTORY_BACKFILL_STARTED` (peer enters `BACKFILLING`).
3. The History team's handler (registered via `service.register_history_handler`) does the actual replay over Distribution's `send_to_peer` to the joiner.
4. When History is done, it calls `service.complete_history_backfill(user_id)` which logs `HISTORY_BACKFILL_COMPLETE` and the peer enters `ACTIVE`.

When no History handler is registered (the demo path), `DiscoveryNode._auto_complete_backfill_handler` (`peer_discovery/network/discovery_node.py:386`) immediately calls both `start_history_backfill` and `complete_history_backfill`, so the joiner becomes ACTIVE without waiting. This is what lets two-laptop tests run without the History module wired in.

`contract_peer_discovery.md` already commits Distribution to **holding traffic** for peers in `BACKFILLING` — they're in the room but not yet a fan-out target. The state machine is what makes that distinction visible.

---

## 9. Subscriber API

`EventNotifier` (`peer_discovery/membership_integration/notifier.py`) is the public read-side of the state machine. Callers subscribe with:

```python
handle = service.subscribe_membership_events(
    callback=on_event,
    from_version=last_seen_version,
)
```

The notifier replays every event with `version > from_version` synchronously, then delivers live events as they arrive. Distribution's `MembershipRouter` uses this to avoid the snapshot-subscription race (snapshot taken at v=42, subscribe with `from_version=42`, miss nothing in between). The dispatch is single-threaded and synchronous, so slow callbacks can block other subscribers — a documented caveat.

---

## 10. Limitations

- **Synchronous subscriber dispatch.** A slow callback delays every subscriber behind it. Acceptable today because the subscriber count is small and callbacks are sub-millisecond, but it is the known scale ceiling.
- **One room per `MembershipService`.** `room_id` is constructor-fixed. Multi-room would mean per-room services, with `room_id` routing happening one level up.
- **`SUSPECTED` is per-presence-tracker, not per-peer-consensus.** Two nodes may simultaneously suspect each other if a switch hiccups; both will fire `DISCONNECT_SUSPECTED`. The state machine accepts both — the dedup guard makes them idempotent — but `originator` is what disambiguates the "who suspected whom" in audit logs.
- **No vector clock on membership events.** We rely on `seq_no` per originator + `term` for ordering. This works because every event has exactly one originator (whoever's coordinator wrote it locally). Chat messages need vector clocks because authorship is logical; membership events don't because authorship is single-writer.

---

## 11. References

- `peer_discovery/membership/event_log.py` — append-only log.
- `peer_discovery/membership/snapshot.py` — projection + transition guards.
- `peer_discovery/membership/presence.py` — SWIM detector.
- `peer_discovery/membership/duplicate_guard.py` — cross-node idempotency.
- `peer_discovery/membership/durability.py` — checkpoint + recover.
- `peer_discovery/membership/models.py` — `MembershipEvent`, `MemberInfo`, enums.
- `peer_discovery/membership_integration/coordinator.py` — single-writer orchestrator.
- `peer_discovery/membership_integration/notifier.py` — subscribe-with-from-version.
- `peer_discovery/membership_integration/service.py` — the public facade.
- `peer_discovery/docs/network_layer.md` — the wire layer that delivers events between nodes.
