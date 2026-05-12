# P2P Chat — Message Distribution Module

**SJSU CS275 Enterprise Applications | Final Project**

This module implements the **Discovery and Message Distribution** component of the class's peer-to-peer distributed chat system. It broadcasts messages to peers over WebSockets using ACK and retry for currently reachable peers.

---

## How It Works

When a node sends a message:
1. It assigns the message a UUID and delivers it locally.
2. It increments its own entry in its **vector clock** and attaches the full vector to the message.
3. It sends the message to **every peer** in the discovery registry concurrently over WebSockets.
4. Each receiving peer sends back an **ACK** confirming receipt.
5. If no ACK arrives within 2 seconds, the sender **retries up to 3 times** before giving up.
6. Each peer that receives a new message checks the UUID. If already seen, it is silently dropped. If new, the peer checks **causal readiness** before delivery.
7. If the message is causally ready (all messages that causally precede it have already been delivered), it is delivered via `on_message` and forwarded to peers. If not, it is held in a **hold-back queue** until its causal predecessors arrive.
8. UUID deduplication ensures every node processes each message **exactly once** regardless of how many paths it arrives through. Causal ordering ensures every node delivers messages in an order consistent with their cause-and-effect relationships.

---

## Project Structure

```
275-Final Project/
├── distribution/
│   ├── __init__.py          # Package exports
│   ├── message.py           # Message dataclass + JSON serialization
│   ├── peer_registry.py     # PeerRegistry interface + InMemoryRegistry
│   ├── broadcast_node.py    # BroadcastNode — WebSocket server + broadcast + ACK/retry
│   └── vector_clock.py      # VectorClock + HoldBackQueue — causal ordering
├── docs/
│   └── vector_clock.md      # Design doc for causal ordering implementation
├── tests/
│   ├── test_vector_clock.py         # Unit tests for vector clock and causal ordering
│   └── test_dedup_loop_prevention.py
├── demo.py                  # Runnable demo: broadcast + causal ordering scenarios
└── requirements.txt         # Python dependencies
```

---

## Setup

**Requirements:** Python 3.11+

```bash
python3.11 -m pip install websockets
```

---

## Quick Start

```bash
python3.11 demo.py
```

**What the demo does:**
- Starts 3 nodes on ports 5001, 5002, and 5003 on localhost
- Node 5001 broadcasts one message to all peers
- Every node receives, ACKs, and prints it exactly once

**Expected output:**
```
[Node :5001] SENT: 'Hello from Node 1!'

[Node :5001] received: 'Hello from Node 1!'  (id=7355eaeb)
[Node :5002] received: 'Hello from Node 1!'  (id=7355eaeb)
[Node :5003] received: 'Hello from Node 1!'  (id=7355eaeb)

[Demo complete — each node should have received the message once]
```

---

## Inputs and Expected Outputs

### `BroadcastNode`

**Inputs (constructor)**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `host` | `str` | Yes | IP address this node listens on. e.g. `"127.0.0.1"` |
| `port` | `int` | Yes | Port this node's WebSocket server binds to. e.g. `5001` |
| `peer_registry` | `PeerRegistry` | Yes | Peer list from the Discovery team. Must implement `get_peers()`. |

**Inputs (methods)**

| Method / Property | Input | Description |
|-------------------|-------|-------------|
| `start()` | None | Starts the WebSocket server. Call before `broadcast()`. |
| `stop()` | None | Shuts down the WebSocket server. |
| `broadcast(message)` | A `Message` object | Sends the message to all peers with ACK + retry. Called by the UI team. |
| `on_message` | A function `(Message) -> None` | Callback set by UI/storage team. Fired once per unique received message. |
| `deduplicate(msg_id)` | A message UUID string | Returns `True` if new (and marks seen), `False` if already seen. |

**Expected outputs**

| Action | Expected behaviour |
|--------|-------------------|
| `node.broadcast(msg)` | `on_message` fires on this node immediately. All peers receive the message and ACK back. Each peer's `on_message` fires exactly once. |
| Peer ACKs successfully | Delivery confirmed. No retry needed. |
| Peer does not ACK within 2s | Sender retries. Attempts 1 → 2 → 3 with 0.5s, 1.0s, 1.5s delays between each. |
| All 3 retries fail | Warning is logged. That peer is marked as unreachable for this message. Recovery/Storage team handles replay when the peer returns. |
| Duplicate message arrives | ACK is still sent so the sender stops retrying, but `on_message` is NOT called again and the message is NOT forwarded again. |
| Peer is offline at broadcast time | All retries fail; warning logged. Other peers are unaffected. |

---

### `Message`

**Inputs (constructor)**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `content` | `str` | Yes | — | The chat message text typed by the user. |
| `sender` | `str` | Yes | — | `"host:port"` of the originating node. e.g. `"127.0.0.1:5001"` |
| `id` | `str` | No | Auto UUID | Unique message ID used for deduplication. Do not set manually. |
| `timestamp` | `float` | No | `time.time()` | Unix timestamp of when the message was created. |
| `signature` | `str` | No | `""` | Hash/signature filled by the **Security team** before calling `broadcast()`. |
| `ttl` | `int` | No | `10` | Max number of forward hops before propagation stops. |
| `vector_clock` | `dict` | No | `{}` | Logical timestamp attached by `BroadcastNode` at send time. Do not set manually. |

**Expected output of `Message.to_json()`**
```json
{
  "content": "Hello!",
  "sender": "127.0.0.1:5001",
  "id": "7355eaeb-...",
  "timestamp": 1747058342.71,
  "signature": "",
  "ttl": 9,
  "vector_clock": {"127.0.0.1:5001": 3, "127.0.0.1:5002": 1}
}
```

---

### `PeerRegistry` (Discovery team's interface)

**Input to `BroadcastNode`:** any object that implements:

```python
def get_peers(self) -> list[tuple[str, int]]:
    ...
```

**Expected output of `get_peers()`:** a list of `(host, port)` tuples of all known peers.

```python
[("127.0.0.1", 5001), ("127.0.0.1", 5002), ("127.0.0.1", 5003)]
```

For testing, use the built-in `InMemoryRegistry`:
```python
from distribution import InMemoryRegistry

registry = InMemoryRegistry()
registry.add_peer("127.0.0.1", 5001)
registry.add_peer("127.0.0.1", 5002)
```

---

## Integration Guide for Other Teams

### UI Team
```python
from distribution import BroadcastNode, Message

# 1. Get registry from Discovery team, create node
node = BroadcastNode("127.0.0.1", 5000, discovery_registry)

# 2. Set callback to display incoming messages
node.on_message = lambda msg: display_in_chat(msg.content, msg.sender, msg.timestamp)

# 3. Start the node
node.start()

# 4. When user hits send:
node.broadcast(Message(content=user_input, sender=node.address))
```

### Security Team
Fill `Message.signature` before the message reaches `broadcast()`:
```python
msg = Message(content="Hello!", sender=node.address)
msg.signature = your_hash_function(msg)
node.broadcast(msg)
```
Do not include `ttl` in a long-lived content signature. TTL is transport metadata
and changes at each forwarding hop. Sign stable fields such as message id, sender,
timestamp, and payload, or coordinate a separate hop-by-hop signature if needed.

### Discovery Team
Subclass `PeerRegistry` and implement `get_peers()`:
```python
from distribution import PeerRegistry

class YourRegistry(PeerRegistry):
    def get_peers(self) -> list[tuple[str, int]]:
        # return your live peer list here
        ...
```
Pass an instance to `BroadcastNode(host, port, YourRegistry())`.

### Recovery & Storage Team
Hook into `on_message` to log every message for backlog/replay:
```python
node.on_message = lambda msg: storage.append(msg)
```
Use `node.deduplicate(msg.id)` to gate your own processing if needed:
```python
if node.deduplicate(msg.id):
    storage.append(msg)
```
When a peer comes back online after missing messages, your team is responsible for replaying the backlog to that node — our module logs a warning for every peer that exhausted all retries, which you can use as a signal.

---

## Delivery Behavior

| Scenario | Outcome |
|----------|---------|
| Peer is online and reachable | Guaranteed — ACK confirms receipt |
| Peer is slow / temporarily unreachable | Retried up to 3 times with backoff |
| Peer is offline for the entire broadcast | Not delivered — Recovery/Storage team replays on reconnect |

**What this module guarantees:** every currently-online peer processes each message ID at most once, and `on_message` fires in causal order — if message A causally precedes message B (the sender of B had observed A before sending B), every peer delivers A before B.
**What it does not guarantee:** delivery to peers that are offline, or a total order among causally unrelated concurrent messages. Offline delivery is handled by the Recovery & Storage team via message backlog replay.

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Broadcast to all peers | Guarantees no peer is skipped by chance — unlike random gossip fanout. |
| ACK + retry | Confirms each delivery; retries transient failures before giving up. |
| Exponential-style backoff | Avoids hammering a temporarily slow peer — retries at 0.5s, 1.0s, 1.5s. |
| WebSocket transport | Full-duplex — ACK travels back on the same connection without opening a second socket. |
| UUID deduplication | Ensures each message is processed exactly once even when multiple forward paths converge on the same node. |
| Atomic `deduplicate()` | Check-and-mark in a single lock acquisition — prevents race conditions under concurrent delivery. |
| `PeerRegistry` interface | Decouples distribution from discovery — swap implementations with no changes to broadcast logic. |
| `asyncio` event loop per node | WebSocket server runs on a background thread; `broadcast()` stays callable from sync code. |
| Vector clocks for causal ordering | Wall-clock timestamps cannot establish causality across machines with drifting clocks. Vector clocks assign a logical timestamp that encodes which messages a sender had observed, enabling receivers to detect and buffer out-of-order deliveries. |
| Hold-back queue | Messages whose causal predecessors have not yet arrived are buffered rather than delivered immediately. When a predecessor is delivered, the queue is drained iteratively so that cascading dependencies resolve in a single pass. |
| Causal ordering only (not total ordering) | Total ordering requires a central coordinator, which is incompatible with the peer-to-peer design. Causal ordering is the strongest guarantee achievable without a coordinator and is sufficient for chat: users only require that replies appear after the messages they reply to. |
