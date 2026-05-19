# Class Report — Message Distribution Section

**Team:** Bhuvana (POC), Asha, Manasa, Anukrithi, Shamathmika
**Module:** Message Distribution
**Date:** 2026-05-13

---

## 1. Overview

The Message Distribution module is the transport and delivery layer of the class's peer-to-peer chat system. In a P2P topology there is no central broker, so every peer must cooperate to propagate messages such that every participant receives every message exactly once, in a coherent order, even when peers join, leave, or fail mid-conversation.

Our module provides four guarantees to the layers above it:

1. **Reachability.** Every message originated by any peer reaches every currently-online peer.
2. **Exactly-once per peer.** Each peer delivers each message to its upper layers at most once, identified by UUID.
3. **Loop freedom.** Messages cannot circulate indefinitely; every forwarding chain terminates within a bounded number of hops.
4. **Causal ordering.** If message A causally precedes message B, every peer delivers A before B.

---

## 2. Architecture

```
             ┌──────────────────────────────────────────────┐
             │                   UI layer                   │
             └──────────┬────────────────────────▲──────────┘
                        │ broadcast(msg)         │ on_message(msg)
                        ▼                        │
             ┌──────────────────────────────────────────────┐
             │              BroadcastNode                   │
             │   dedup · vector clock · hold-back · ACK     │
             └─────┬────────┬────────▲─────────▲────────────┘
                   │        │        │         │
                   ▼        ▼        │         │
            ┌──────────┐ ┌─────────┐ │         │
            │ WebSocket│ │ WebSocket│ │         │
            │  send    │ │  send    │ │         │
            └──────────┘ └──────────┘ │         │
                                      │         │
             (peer list)               (remote sends via WebSocket)
                   │
                   ▼
             ┌──────────────────────────────────────────────┐
             │    MembershipRouter (PeerRegistry)           │
             │    ← subscribes to MembershipService         │
             └──────────────────────────────────────────────┘
```

Public API is three entry points on `BroadcastNode`: `start()`, `broadcast(msg)`, and the `on_message` callback. Everything else is internal.

---

## 3. Design

### 3.1 Broadcast to all peers with ACK + retry

When a node originates or receives a new message, it forwards the message to every other ACTIVE peer concurrently. Each receiving peer sends back an ACK. If no ACK arrives within 2 seconds, the sender retries up to 3 times with linear backoff (0.5 s, 1.0 s, 1.5 s).

We chose this over random-fanout gossip because it guarantees no peer is skipped by chance, at the cost of higher send volume per message. At the demo scale (up to ~20 peers per room) this cost is negligible, and it makes the delivery guarantee defensible: "every online peer receives every message" rather than "every peer probably receives every message."

### 3.2 De-duplication

Each node maintains a thread-safe set of seen message UUIDs. A single atomic `deduplicate(msg_id)` operation checks-and-marks under one lock acquisition, so two concurrent forwards of the same message cannot both pass.

De-dup runs **before** causal-ordering logic. This ensures the hold-back queue never accumulates duplicates and that a duplicate message does not re-trigger delivery or forwarding.

### 3.3 Loop prevention

Three independent safety nets prevent infinite circulation:

1. The seen-set (§3.2) — a peer drops any message it has already forwarded.
2. A TTL field on each message (default 10), decremented per hop; messages with `ttl == 0` are delivered locally but not forwarded.
3. Sender exclusion — a node never forwards a message back to the peer identified as its original sender.

Any one of these would suffice in isolation; the three together give us a hard guarantee against cycles.

### 3.4 Causal ordering via vector clocks

Gossip alone does not preserve order: two messages following different random paths can arrive at a third peer in reversed order. Wall-clock timestamps cannot fix this — clocks drift, and there is no global clock in a P2P network.

Each node maintains a vector clock: a dict mapping peer addresses to integer counters. On send, the originator **signs the message first, then increments its own VC entry** and attaches the snapshot. This ordering is a correctness requirement: if signing fails, the VC must not advance — a skipped sequence number would permanently stall every subsequent message in every peer's hold-back queue. On receive, a peer checks whether the message is **causally ready** — whether every message the sender had seen at send time has already been delivered locally. If not ready, the message is buffered in a hold-back queue and re-checked after each subsequent delivery.

The readiness check (`distribution/vector_clock.py::VectorClock.is_ready`) is:

1. `VC_M[sender] == VC_R[sender] + 1` — this is the next message from the sender, no gap.
2. For all other entries, `VC_M[k] ≤ VC_R[k]` — we have already seen everything the sender had seen.

On delivery, the receiver merges the incoming vector clock element-wise (`max`) into its own, then drains the hold-back queue — released messages may unblock others in a cascade.

**Liveness under quiet networks.** `drain()` is only called when a new message arrives. In a network that goes quiet after a message is buffered, the hold-back queue would never be re-checked. We address this with two mechanisms: a `HOLDBACK_TIMEOUT` of 5 seconds (messages waiting longer are delivered out-of-order with a warning), and a background `_holdback_drain_task` coroutine that calls `drain()` every 2 seconds so the timeout fires promptly regardless of traffic.

This gives us **causal order** (CO): if A → B, every peer delivers A before B. It does not give us total order across concurrent messages — two messages with no causal relationship may interleave differently at different peers. This is acceptable for chat: users observe no causal dependency between concurrent messages.

### 3.5 Transport — WebSockets

We use WebSockets rather than raw TCP because it is full-duplex on a single connection (the ACK travels back on the same socket) and because it keeps the door open for browser-based peers in future work. Each node runs an `asyncio` event loop on a background thread; `broadcast()` is a sync-friendly entry point that schedules work on the loop.

### 3.6 Integration with Peer Discovery

`MembershipRouter` is a `PeerRegistry` implementation that consumes the Peer Discovery team's `MembershipService`. It subscribes to six event types:

| Event | Router behaviour |
|---|---|
| `JOIN_ACCEPTED` | Add peer to hold-back (not yet eligible for delivery) |
| `HISTORY_BACKFILL_COMPLETE` | Promote hold-back → ACTIVE |
| `DISCONNECT_SUSPECTED` | Demote ACTIVE → hold-back |
| `RECONNECTED` | Promote hold-back → ACTIVE |
| `LEAVE_CONFIRMED` / `DISCONNECT_TIMEOUT` | Remove from all routing |

ACTIVE peers receive real-time broadcasts. Held peers are excluded until their state resolves. This keeps messages from being wasted on peers still replaying history, and lets Peer Discovery own the state machine without changing our broadcast logic.

---

## 4. Implementation

| File | Lines | Responsibility |
|---|---|---|
| `distribution/message.py` | 29 | `Message` dataclass + JSON I/O |
| `distribution/broadcast_node.py` | 224 | WebSocket server, broadcast loop, ACK + retry, dedup, VC integration |
| `distribution/vector_clock.py` | 64 | `VectorClock`, `HoldBackQueue` |
| `distribution/peer_registry.py` | 41 | `PeerRegistry` abstract + `InMemoryRegistry` |
| `distribution/membership_router.py` | 219 | `PeerRegistry` bound to Peer Discovery's `MembershipService` |
| `demo.py` | 147 | 10-node broadcast + 4 causal-ordering scenarios |

Total: ~720 lines of production Python. Standard library plus `websockets` (one external dependency).

---

## 5. Testing

### 5.1 Unit tests

- `tests/test_dedup_loop_prevention.py` — 6 tests covering atomic dedup, TTL-0 non-forward, TTL-decrement, ttl-mutation safety, local duplicate broadcast, fast-fail when `websockets` is missing.
- `tests/test_vector_clock.py` — 24 tests covering VC operations (`increment`, `merge`, `snapshot`); the full `is_ready` decision tree; `HoldBackQueue` drain and cascade; `BroadcastNode` causal send/receive; JSON round-trip with the new `vector_clock` field.

### 5.2 End-to-end integration tests

- `tests/test_integration.py` — 4 tests that wire `BroadcastNode` together with stubs for Security, Peer Discovery (via `InMemoryRegistry`), and History, and assert:
  1. A signed message reaches every peer exactly once and passes signature verification.
  2. A message with a tampered signature is dropped by the receiving storage layer.
  3. Broadcasting the same message twice still delivers once per peer.
  4. Multiple `on_message` listeners (UI + storage) all receive every delivered message.

All 34 tests pass. The integration test suite serves as the contract regression for cross-team integration — when the real Security and History modules ship, we swap the stubs for them and re-run.

---

## 6. Trade-offs and alternatives considered

| Decision | Alternatives | Why we chose this |
|---|---|---|
| Broadcast to all peers | Random-fanout gossip | Determinism for a small peer set. Gossip is the right call at thousands of peers, not tens. |
| ACK + retry | Fire-and-forget | At-least-once-for-online-peers gives History a clear "this peer missed it" signal. |
| Causal order (not total) | Total order via coordinator | Total order breaks the P2P design goal and is unnecessary for chat. |
| WebSockets | Raw TCP, MPI | WS is full-duplex and future-proofs browser peers. TCP would also work; MPI assumes static rank count, which breaks dynamic join. |
| Vector clock attached to every message | Batched VC exchange | Simpler, debuggable; overhead is negligible at demo scale. |
| Python standard library + `websockets` only | `aiohttp`, `grpc`, etc. | Shorter integration path for the class. |

---

## 7. Known limitations

1. **Seen-set grows without bound.** Production would bound by time window or LRU cap. Acceptable at demo scale.
2. **Hold-back queue degrades to out-of-order delivery** if a causal predecessor is permanently lost. After a 5-second timeout, the stuck message is delivered out-of-order with a warning rather than stalling the queue indefinitely.
3. **Offline peers do not receive messages sent while they were offline.** Recovery is the History team's replay path.
4. **No wire-level encryption.** Security signs; encryption is a stretch.
5. **Single global room.** `Message` carries no `room_id`; extending to multi-room is a straightforward additive change.
6. **MPI transport dropped.** WebSockets + retries cover the P2P requirement, and MPI's fixed-rank model contradicts dynamic peer join/leave.

---

## 8. Contributions

| Member | Work |
|---|---|
| Bhuvana (POC) | Integration contracts (Security, Peer Discovery, History); end-to-end integration test; this report section; README refresh; PR/merge coordination |
| Asha | `BroadcastNode` implementation; vector clock integration into broadcast/receive |
| Anukrithi | De-duplication (atomic seen-set); loop prevention (TTL + sender exclusion); unit tests |
| Shamathmika | Vector clock design doc; `VectorClock` + `HoldBackQueue` implementation and unit tests; hold-back timeout and background drain task for liveness under quiet networks |
| Manasa | WebSocket transport inside `BroadcastNode` |
| Peer-integration teammate | `MembershipRouter` wired to Peer Discovery's `MembershipService` |

---

## 9. References

- Demers, A. et al. *Epidemic Algorithms for Replicated Database Maintenance.* PODC '87.
- Lamport, L. *Time, Clocks, and the Ordering of Events in a Distributed System.* CACM '78.
- Fidge, C. *Timestamps in Message-Passing Systems That Preserve the Partial Ordering.* 1988.
- Birman, K. and Joseph, T. *Reliable Communication in the Presence of Failures.* ACM TOCS '87.
