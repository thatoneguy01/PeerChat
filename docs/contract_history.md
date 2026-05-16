# Integration Contract — History / Recovery & Storage Team

**Owner on our side:** Bhuvana (Message Distribution POC)
**Audience:** History team POC
**Status:** Draft for sign-off, 2026-05-12
**Relevant MD code:** `distribution/broadcast_node.py`

---

## What Message Distribution provides you

Two hooks, nothing more:

1. **`BroadcastNode.on_message`** — callback fired exactly once per unique message, in **causal order**. Register your storage writer here.
2. **`BroadcastNode.deduplicate(msg_id)`** — atomic check-and-mark. Use it if you want to gate your own processing.

That's the whole API surface between us.

## The interface

```python
from distribution import BroadcastNode, Message

node = BroadcastNode(host, port, peer_registry)
node.on_message = lambda msg: storage.append(msg)   # YOU register this
node.start()
```

- `msg` is the full `Message` dataclass (`content`, `sender`, `id`, `timestamp`, `signature`, `ttl`, `vector_clock`).
- Fires on **every** unique message the node delivers — originated or received.
- Already de-duplicated; you will not see the same `msg.id` twice on the same node.
- Already in **causal order** when the vector clock layer has buffered predecessors.

## If you also want to gate your own processing

```python
def on_message(msg):
    if node.deduplicate(msg.id):
        storage.append(msg)
    # else: duplicate, already in log
```

This is optional — MD already dedupes before firing `on_message`. But it's useful if storage is wired to multiple nodes or you want a defensive second check.

## Call order on receive

```
WebSocket receive
    → BroadcastNode._receive
    → deduplicate()  [MD owns]
    → vector-clock ready-check  [MD owns]
    → on_message(msg)  [YOU]
    → (also: MD forwards to other peers)
```

Your `on_message` is called **after** dedup and causal check. You see each message once, in causal order.

## Co-existing with UI team

UI team also uses `on_message`. There's only one callback slot. Recommended fan-out shim:

```python
# shared/listeners.py
from typing import Callable, List
from distribution import Message

class Listeners:
    def __init__(self):
        self._fns: List[Callable[[Message], None]] = []
    def register(self, fn): self._fns.append(fn)
    def dispatch(self, msg):
        for fn in self._fns:
            try: fn(msg)
            except Exception: pass   # don't let one listener break others
```

Wire once per node:
```python
listeners = Listeners()
listeners.register(storage.append)      # YOU
listeners.register(ui.display)          # UI team
node.on_message = listeners.dispatch
```

Coordinate with the UI team on who owns this shim. Default: whichever team ships first.

## Replay to newly-joined peers — critical rule

When a new peer joins (Peer Discovery fires `JOIN_ACCEPTED` then `HISTORY_BACKFILL_COMPLETE`), you replay the backlog.

**Do NOT replay via `node.broadcast()`.** If you do, every peer receives every old message a second time — gossip will re-deliver them, and `Message.id` dedup will save you per-node but waste massive bandwidth.

Correct approach:

1. Peer Discovery tells you a new peer joined (you coordinate that channel with them — not via us).
2. Send the backlog **directly** to the new peer over a separate WebSocket / TCP connection.
3. On the receiving side, feed the backlog into storage, optionally bypassing `broadcast()` entirely.

MD does not need to know this happens. We do not want replayed history re-entering gossip.

## What we guarantee

| Guarantee | Detail |
|---|---|
| Each message arrives once | `on_message` fires at most once per `msg.id` per node |
| Causal order | If A causally precedes B, A is delivered before B |
| Full payload | You get the entire `Message` dataclass — all fields preserved |
| No silent drops | Every message that passes dedup + causal is delivered |

## What we do NOT guarantee

| Non-guarantee | Implication for you |
|---|---|
| Total order across concurrent messages | Different peers' logs may differ in the order of causally unrelated messages. Design your replay not to assume a single canonical order. |
| Delivery to offline peers | If a peer is offline during broadcast, it gets a warning log on our side. You own the recovery. |
| Durability on our side | We don't persist messages. If a peer crashes, its in-flight state is gone. You are the system's durability layer. |
| Retransmit to re-connected peers | After `RECONNECTED` we resume sending new broadcasts, but don't replay anything they missed. That's your replay path. |

## Chunking / large payloads

The board mentions chunked transfer for history. MD doesn't care how you chunk — but:

- Each chunk sent via `broadcast()` will be dedup'd by `msg.id`. Give each chunk a unique `id`.
- Each chunk fans out to all peers. A 100-chunk file = 100× the broadcast cost. Strongly consider a direct peer-to-peer transfer bypassing gossip for replay chunks.

## Open questions we need you to confirm by EOD 2026-05-12

- **Q1:** Confirm you replay history **outside** `node.broadcast()`. Yes/no.
- **Q2:** Who owns the listener fan-out shim — you or UI? Propose: you, since storage has a harder durability requirement.
- **Q3:** How does Peer Discovery signal "new peer needs backlog" to you? We don't know; please confirm with them and let us know if you need anything from us in that channel.
- **Q4:** Do you want a pre-delivery hook (e.g., `on_before_deliver(msg)`) so you can checkpoint **before** the UI sees the message? Default: no. Easy to add if yes.
