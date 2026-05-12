# P2P Chat — Message Distribution Module

**SJSU CS275 Enterprise Applications | Final Project**

This module implements the **Discovery and Message Distribution** component of the class's peer-to-peer distributed chat system. It uses an epidemic (gossip) protocol to broadcast messages to all peers without any central server.

---

## How It Works

When a node sends a message:
1. It assigns the message a UUID and delivers it locally.
2. It forwards the message to **3 randomly chosen peers** (fanout).
3. Each receiving peer checks if it has already seen the UUID — if not, it delivers the message locally and forwards it to 3 more random peers.
4. This continues until every peer has seen the message or the TTL (hop limit) reaches 0.

This approach ensures every peer receives the message while avoiding duplicates and infinite forwarding loops.

---

## Project Structure

```
275-Final Project/
├── distribution/
│   ├── __init__.py          # Package exports
│   ├── message.py           # Message dataclass + JSON serialization
│   ├── peer_registry.py     # PeerRegistry interface + InMemoryRegistry
│   └── gossip_node.py       # GossipNode — TCP server + gossip logic
└── demo.py                  # Runnable 3-node demo
```

---

## Quick Start

**Requirements:** Python 3.8+ — no external dependencies.

```bash
python3 demo.py
```

Expected output:
```
[Node :5001] SENT: 'Hello from Node 1!'

[Node :5001] received: 'Hello from Node 1!'  (id=...)
[Node :5002] received: 'Hello from Node 1!'  (id=...)
[Node :5003] received: 'Hello from Node 1!'  (id=...)
```

---

## API Reference

### `GossipNode`

```python
from distribution import GossipNode, InMemoryRegistry, Message

registry = InMemoryRegistry()
registry.add_peer("127.0.0.1", 5001)
registry.add_peer("127.0.0.1", 5002)

node = GossipNode(host="127.0.0.1", port=5000, peer_registry=registry, fanout=3)
node.on_message = lambda msg: print(f"Received: {msg.content}")
node.start()

node.broadcast(Message(content="Hello!", sender="127.0.0.1:5000"))

node.stop()
```

| Method | Description |
|--------|-------------|
| `start()` | Opens the TCP listening socket and begins accepting gossip connections. |
| `stop()` | Closes the listening socket. |
| `broadcast(message)` | Sends a message into the gossip network. Called by the UI team. |
| `on_message` | Callback `(Message) -> None` set by the UI team. Fires once per unique message. |

### `Message`

| Field | Type | Description |
|-------|------|-------------|
| `content` | `str` | The chat message text. |
| `sender` | `str` | `"host:port"` of the originating node. |
| `id` | `str` | Auto-generated UUID — used for deduplication. |
| `timestamp` | `float` | Unix timestamp set at creation. |
| `signature` | `str` | **Set by the Security team** before calling `broadcast()`. |
| `ttl` | `int` | Hop limit (default 10). Decremented at each forward step. |

---

## Integration Guide for Other Teams

### UI Team
1. Create a `GossipNode` and pass it a peer registry from the Discovery team.
2. Set `node.on_message` to your display handler before calling `node.start()`.
3. Call `node.broadcast(Message(content=text, sender=node.address))` when the user sends a message.

### Security Team
Fill `Message.signature` before the message reaches `broadcast()`:
```python
msg = Message(content="Hello!", sender=node.address)
msg.signature = your_hash_function(msg)
node.broadcast(msg)
```

### Discovery Team
Subclass `PeerRegistry` and implement `get_peers()`:
```python
from distribution import PeerRegistry

class YourRegistry(PeerRegistry):
    def get_peers(self) -> list[tuple[str, int]]:
        # return your live peer list here
        ...
```
Pass an instance of your registry to `GossipNode(...)`.

### Recovery & Storage Team
Subscribe to `node.on_message` to log incoming messages for your backlog:
```python
node.on_message = lambda msg: storage.append(msg)
```

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Gossip / epidemic broadcast | Scales to large networks; tolerates peers going offline mid-broadcast. |
| UUID deduplication | Each node tracks seen message IDs so no message is delivered or forwarded twice. |
| TTL hop limit | Prevents runaway forwarding in case the seen-set is somehow bypassed. |
| Length-prefixed TCP framing | Simple, reliable — no external libraries needed. |
| `PeerRegistry` interface | Decouples us from the Discovery team's implementation; swap with no changes to gossip logic. |
| Stdlib only | No pip installs — everyone can run it immediately. |
