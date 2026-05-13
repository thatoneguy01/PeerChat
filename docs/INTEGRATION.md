# Integration Guide — Message Distribution

**Module:** Message Distribution (MD)
**POC:** Bhuvana
**Status:** Current as of 2026-05-12

One-page integration summary for the three teams that plug into MD. For details, click through to each contract.

---

## What MD is

A single public class, `BroadcastNode`, that delivers messages to every currently-reachable peer, exactly once per peer, in causal order.

```python
from distribution import BroadcastNode, Message

node = BroadcastNode(host, port, peer_registry)
node.on_message = lambda msg: handle(msg)   # register receive hook
node.start()
node.broadcast(Message(content="hi", sender=node.address))
node.send_to_peer("127.0.0.1", 5004, replay_chunk_msg)
node.stop()
```

## The three integrations

### 1. Peer Discovery → MembershipRouter
**Status:** Already wired. See `distribution/membership_router.py` + [`contract_peer_discovery.md`](contract_peer_discovery.md).

- You ship a `MembershipService` exposing `get_membership_snapshot()` and `subscribe_membership_events(callback, from_version)`.
- We consume it via `MembershipRouter`, which is a drop-in `PeerRegistry`.
- Membership states we care about: `ACTIVE`, `BACKFILLING`, `SUSPECTED`, `DISCONNECTED`/`LEFT`/`LEAVING`.
- Events we subscribe to: `JOIN_ACCEPTED`, `HISTORY_BACKFILL_COMPLETE`, `DISCONNECT_SUSPECTED`, `RECONNECTED`, `LEAVE_CONFIRMED`, `DISCONNECT_TIMEOUT`.
- **Action needed:** confirm the event-name schema is final.

### 2. Security → sign / verify
**Status:** Interface documented; implementation on your side. See [`contract_security.md`](contract_security.md).

- You ship `sign(msg) → msg` and `verify(msg) → bool`.
- Originator calls `sign()` **before** `node.broadcast(msg)`.
- Receiver calls `verify()` **inside** its `on_message` handler (MD doesn't enforce verification — policy decision is yours).
- Sign only the stable fields: `id`, `sender`, `timestamp`, `content` (canonicalized with `signature=""`).
- **Exclude** `ttl` **and** `vector_clock` from the signed payload — both are mutated in-transit (TTL decrements per hop; VC is filled by `broadcast()` after you sign).
- **Action needed:** confirm the `ttl` + `vector_clock` exclusion rule is acceptable.

### 3. History / Recovery → on_message listener + direct replay
**Status:** Interface documented; implementation on your side. See [`contract_history.md`](contract_history.md).

- You register a listener on `node.on_message` to log every delivered message (causal-ordered, dedup'd).
- **Critical:** replay history to new peers with `node.send_to_peer(host, port, msg)`, not `node.broadcast()`, or messages re-gossip and bandwidth explodes.
- Direct sends reuse the same WebSocket ACK/retry path, but copy the message with `ttl=0` so the target receives the chunk and does not forward it to the room.
- Coordinate with Peer Discovery on the "new peer needs backlog" signal — MD isn't in that path.
- **Action needed:** confirm direct-replay rule + ownership of the listener fan-out shim.

## UI team (no separate contract)

```python
node = BroadcastNode(host, port, registry)
node.on_message = lambda msg: display_in_chat(msg.content, msg.sender, msg.timestamp)
node.start()

# on user send:
msg = Message(content=user_text, sender=node.address)
msg = security.sign(msg)           # after Security ships
node.broadcast(msg)
```

## Shared `Message` schema

```python
@dataclass
class Message:
    content: str          # chat text
    sender: str           # "host:port" of originator
    id: str               # auto UUID, don't set manually
    timestamp: float      # auto, time.time()
    signature: str        # Security fills
    ttl: int              # default 10, decremented per hop, do NOT sign
    vector_clock: dict    # BroadcastNode fills in _do_broadcast
```

JSON round-trippable. Backward compatible with old messages missing `vector_clock`.

## Call order — who does what, when

### Send path
```
UI: build Message
UI: (optionally) Security.sign(msg)
UI: node.broadcast(msg)
    MD: deduplicate(msg.id)  [atomic]
    MD: vector_clock.increment(self.address)
    MD: msg.vector_clock = snapshot()
    MD: on_message(msg)  [local delivery]
    MD: forward to every peer with ACK+retry
```

### Direct history replay path
```
History: build replay chunk Message
History: node.send_to_peer(target_host, target_port, msg)
    MD: copy msg with ttl=0
    MD: send only to that peer with ACK+retry
Target MD: deduplicate(msg.id)
Target MD: on_message(msg)
Target MD: ttl == 0, so do not forward
```

### Receive path
```
MD: WS receive
MD: deduplicate(msg.id)  [drop if seen]
MD: vector_clock.is_ready(msg)?
    yes → merge VC, on_message(msg), drain hold-back
    no  → add to hold-back queue, wait for predecessors
MD: if ttl > 0, forward to other peers
```

Your `on_message` sees each message once, in causal order, after dedup.

## Delivery guarantees

| Claim | Reality |
|---|---|
| Every online peer receives every message | Yes, with 3-retry ACK |
| Exactly once per peer | Yes — atomic dedup by UUID |
| Causal order preserved | Yes — vector clocks + hold-back queue |
| Offline peers receive on reconnect | No — History team's replay path |
| Total order across concurrent messages | No — that's a non-goal |

## Deadlines

- **EOD 2026-05-12:** Contract sign-off from Peer Discovery, Security, History POCs.
- **Tomorrow 2026-05-13:** Final merge, E2E test green, class report submitted.

## Links

- Peer Discovery contract → `docs/contract_peer_discovery.md`
- Security contract → `docs/contract_security.md`
- History contract → `docs/contract_history.md`
- Vector clock design → `docs/vector_clock.md`
- Team PRD → `docs/PRD.md`
