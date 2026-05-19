# Vector Clock & Causal Ordering

**Component:** Message Distribution  
**Author:** shamathmika

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

## 5. Implementation

### 5.1 `distribution/message.py`: `vector_clock` field

The `Message` dataclass carries one field for causal ordering:

```
vector_clock: dict[str, int]   # snapshot of sender's VC at send time; default {}
```

`Message` serialises via `dataclasses.asdict` → JSON and deserialises via `Message(**json.loads(...))`. The default `{}` means older nodes that do not carry a vector clock are treated as having a zeroed clock and are delivered immediately.

### 5.2 `distribution/vector_clock.py`

A standalone module with no dependencies on gossip internals, containing two classes:

- **`VectorClock`**: wraps a `dict[str, int]`, provides `increment(node_id)`, `merge(other)` (element-wise max), and `is_ready(msg)` (the readiness check from Section 4).
- **`HoldBackQueue`**: buffer of `(enqueue_time, Message)` pairs. Exposes `add(msg)` and `drain(vc) -> list[Message]`. `drain` first flushes any messages that have exceeded `HOLDBACK_TIMEOUT` (5 seconds) delivering them out-of-order with a warning, then does a cascading pass releasing all causally-ready messages. No internal locking is needed since all causal state is accessed from within `BroadcastNode`'s asyncio event loop, which is single-threaded.

Keeping this in its own module makes it independently testable without spinning up WebSocket servers.

### 5.3 `distribution/broadcast_node.py`: causal layer integration

`BroadcastNode` owns a `VectorClock` and a `HoldBackQueue`. The key design points:

**`_do_broadcast(message)`** — sign first, then increment VC:

```python
if not self._sign_outgoing(message):
    return False          # sign failure: VC is NOT incremented, no gap left
self._vc.increment(self.address)
message.vector_clock = self._vc.snapshot()
```

Signing before incrementing is a correctness requirement: if signing fails (e.g. key not yet loaded), the VC must not advance. A gap in the sender's VC sequence would permanently stall all subsequent messages in every other node's hold-back queue.

**`_receive(message)`** — causal delivery:
1. Dedup check via `deduplicate()` — runs first, so the hold-back queue never accumulates duplicates.
2. Check `VectorClock.is_ready(message)`.
   - Ready: merge incoming VC, deliver via `on_message`, then call `drain()` for cascading releases.
   - Not ready: add to `HoldBackQueue`; then call `drain()` in case timeout-expired messages are ready.
3. Forward to peers regardless of causal readiness — other peers make their own causal decision.

**`_holdback_drain_task`** — background coroutine running every 2 seconds:

```python
async def _holdback_drain_task(self) -> None:
    while not self._stop_event.is_set():
        await asyncio.sleep(HOLDBACK_DRAIN_INTERVAL)   # 2 s
        if self._hold_back._queue:
            released = self._hold_back.drain(self._vc)
            for msg in released:
                if self.on_message:
                    self.on_message(msg)
```

Without this, the hold-back timeout only fires when a new message arrives to trigger `drain()`. In a quiet network a stuck message could sit well past 5 seconds. The background task ensures liveness even when no new messages are arriving.

The public API (`start`, `stop`, `broadcast`, `on_message`) is unchanged.

---

## 6. Integration Points with Other Teams

| Team | Impact |
|---|---|
| **UI** | None. `on_message` still fires once per message, now in causal order. |
| **Security** | None. Distribution signs messages through Security, and `vector_clock` is excluded from the signed payload because Distribution mutates it for causal ordering. |
| **Recovery & Storage** | Messages logged via `on_message` will now arrive in causal order, making log replay naturally ordered. No change needed on their side. |
| **Discovery** | None. `PeerRegistry.get_peers()` interface is unchanged. |

---

## 7. Known Limitations

**Hold-back queue timeout.** If a predecessor message is permanently lost (sender crashes before all retries succeed), the queue would stall indefinitely without intervention. The implementation addresses this with a 5-second `HOLDBACK_TIMEOUT`: messages waiting longer than that are delivered out-of-order with a warning log, unblocking the queue. A background drain task (`_holdback_drain_task`) runs every 2 seconds so the timeout fires promptly even in a quiet network where no new messages arrive to trigger a drain.

**New nodes start with a zeroed clock.** A node joining mid-conversation has `VC[k] = 0` for all `k`. Messages addressed to it pass the readiness check immediately, so it receives causally ordered live messages but not older history. That is the Recovery & Storage team's responsibility (log replay + `sync_vector_clock`).

**Concurrent messages have no guaranteed order.** Two messages with no causal relationship between them may arrive in different orders at different nodes. This is by design and acceptable for chat: users observe no causal dependency between concurrent messages, so the interleaving difference is not user-visible.

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
