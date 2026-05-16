# Vector Clock & Causal Ordering Implementation Plan

**Component:** Message Distribution  
**Author:** shamathmika  
**Branch:** `feat/message-distribution`

---

## 1. The Problem

The existing broadcast protocol (`BroadcastNode`) reliably delivers every message to every peer exactly once. What it does **not** guarantee is the *order* in which those messages are delivered.

Because messages travel over independent WebSocket connections, two messages sent in quick succession, say, Alice replies to Bob, may arrive at a third peer with Alice's reply showing up *before* Bob's original message. The chat log becomes causally incoherent: a reply appears before the message it responds to.

Wall-clock timestamps (`Message.timestamp`) cannot fix this. Clocks on different machines drift; there is no global clock in a P2P network.

---

## 2. What Is a Vector Clock?

A **vector clock** is a logical timestamp that captures causality rather than physical time.

Each node maintains a vector, a dictionary mapping every known node address to an integer counter:

```
VC = { "127.0.0.1:5001": 2, "127.0.0.1:5002": 1, "127.0.0.1:5003": 0 }
```

The rules are:

1. **On send:** The sender increments its own entry, then attaches the entire vector to the message.
2. **On receive:** Before delivering a message, the receiver checks whether it is *causally ready* (see Section 4). If ready, it delivers and merges the incoming vector into its own (taking the element-wise max). If not ready, it holds the message in a buffer.

This gives every message a precise snapshot of what its sender had already seen when it wrote that message. A receiver can therefore detect when a message references history it has not yet processed.

---

## 3. Causal Ordering Guarantee

Vector clocks enforce **causal ordering (CO)**:

> If event A causally precedes event B (written A → B), then every correct node delivers A before B.

In chat terms: if Alice reads Bob's message and then replies, every peer will see Bob's message before Alice's reply, regardless of which gossip path each message travelled.

This is weaker than **total ordering** (which would require a coordinator and is incompatible with the P2P design goal), but it is the strongest ordering guarantee achievable without a central server. Concurrent messages (ones with no causal relationship) may still be delivered in different orders at different nodes, which is acceptable for a chat application.

---

## 4. Causal Readiness Check

A message `M` sent by node `S` with vector clock `VC_M` is **causally ready** at a receiving node `R` (with local clock `VC_R`) if and only if:

1. `VC_M[S] == VC_R[S] + 1` (this is the *next* message from S; no gap)
2. `VC_M[k] <= VC_R[k]` for all `k ≠ S` (every message that S had seen when it sent M has already been delivered at R)

If either condition fails, R places M in a **hold-back queue** and re-checks readiness each time a new message is delivered. Once the missing predecessors arrive, M is released from the queue and delivered in the correct causal position.

---

## 5. Changes to the Existing Codebase

### 5.1 `distribution/message.py`: add `vector_clock` field

The `Message` dataclass gains one new field:

```
vector_clock: dict[str, int]   # snapshot of sender's VC at send time; default {}
```

Because `Message` already serialises via `dataclasses.asdict` → JSON and deserialises via `Message(**json.loads(...))`, adding a field with a default value is a backward-compatible change. Older nodes that do not yet carry a vector clock will send `{}`, which the causal layer treats as a zeroed clock.

No other changes to `message.py`.

### 5.2 `distribution/vector_clock.py`: new module

A standalone module, no dependencies on gossip internals, responsible for:

- **`VectorClock` class**: wraps a `dict[str, int]`, provides `increment(node_id)`, `merge(other)` (element-wise max), and `is_ready(msg, local_vc)` (the readiness check from Section 4).
- **`HoldBackQueue` class**: buffer of messages waiting on missing predecessors. Exposes `add(msg)` and `drain(local_vc) -> list[Message]` (returns all messages that became ready after a delivery). No internal locking needed; all causal state is accessed from within `BroadcastNode`'s asyncio event loop, which is single-threaded.

Keeping this in its own module makes it independently testable without spinning up WebSocket servers.

### 5.3 `distribution/broadcast_node.py`: integrate causal layer

`BroadcastNode` owns a `VectorClock` and a `HoldBackQueue`. The touch points are:

**`_do_broadcast(message)`**: increments the local VC entry for `self.address` and writes the resulting vector into `message.vector_clock` before delivering locally and forwarding to peers. This is the async coroutine scheduled by the public `broadcast()` method.

**`_receive(message)`**: after deduplication via `deduplicate()`, instead of calling `on_message` directly:
1. Check `VectorClock.is_ready(message)`.
2. If ready: merge incoming VC into local VC, deliver (`on_message`), then call `drain()` to check whether any buffered messages are now ready (delivering them in order).
3. If not ready: add to `HoldBackQueue`.

The existing deduplication logic (`deduplicate()`) is unchanged and still runs first, so the hold-back queue never accumulates duplicates. Forwarding to peers via `_forward()` always happens regardless of causal readiness, so other peers receive the message and can make their own causal decision.

The public API (`start`, `stop`, `broadcast`, `on_message`) is unchanged. Other teams do not need to modify their integration code.

---

## 6. Integration Points with Other Teams

| Team | Impact |
|---|---|
| **UI** | None. `on_message` still fires once per message, now in causal order. |
| **Security** | None. `signature` is still set before `broadcast()`. The vector clock is part of the signed payload. Order of operations: fill `vector_clock`, fill `signature`, call `broadcast()`. |
| **Recovery & Storage** | Messages logged via `on_message` will now arrive in causal order, making log replay naturally ordered. No change needed on their side. |
| **Discovery** | None. `PeerRegistry.get_peers()` interface is unchanged. |

---

## 7. Known Limitations

**Hold-back queue can grow unboundedly** if a predecessor message is permanently lost (e.g., the sender crashes before all retries are exhausted). A real production system would add a timeout that falls back to delivery-in-arrival-order after waiting too long. For this project, `BroadcastNode`'s ACK/retry mechanism (up to 3 attempts per peer) makes permanent message loss unlikely in a local demo, so this is an acceptable simplification.

**New nodes start with a zeroed clock.** A node joining mid-conversation has `VC[k] = 0` for all `k`. Messages addressed to it will pass the readiness check immediately, meaning it may receive causally ordered messages for the ongoing conversation but will not see older history. That is the Recovery & Storage team's responsibility (log replay).

**Concurrent messages have no guaranteed order.** Two messages with no causal relationship between them may arrive in different orders at different nodes. This is by design and is acceptable for a chat application where the users themselves observe no causal dependency between those messages.

---

## 8. Example Walkthrough

Three nodes: A (`:5001`), B (`:5002`), C (`:5003`). All clocks start at `{5001:0, 5002:0, 5003:0}`.

| Step | Event | A's VC | B's VC | C's VC |
|---|---|---|---|---|
| 1 | A sends M1 | `{5001:1, 5002:0, 5003:0}` | (none) | (none) |
| 2 | B receives M1, delivers | `{5001:1, …}` | `{5001:1, 5002:0, 5003:0}` | (none) |
| 3 | B sends M2 (reply to M1) | (none) | `{5001:1, 5002:1, 5003:0}` | (none) |
| 4 | C receives M2 first (arrived via a faster network path) | (none) | (none) | holds M2 in queue |
| 5 | C receives M1, delivers | (none) | (none) | `{5001:1, 5002:0, 5003:0}` |
| 6 | C drains queue: M2 now ready, delivers | (none) | (none) | `{5001:1, 5002:1, 5003:0}` |

C sees M1 then M2, the correct causal order, even though M2 arrived first over the network.
