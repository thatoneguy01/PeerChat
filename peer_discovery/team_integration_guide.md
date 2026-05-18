# Integration Guide — Peer Discovery

**Module:** Peer Discovery & Membership
**Owners:** Himanshu, Ali, Abhishek
**Status:** Current as of 2026-05-18

One-page orientation for the three teams that integrate with Peer Discovery. For per-team contracts (what each side provides + promises + open questions), see [`docs/integration_contracts.md`](docs/integration_contracts.md).

---

## What Peer Discovery is

A single facade, `MembershipService`, that maintains room membership as an event-sourced state machine, plus a `DiscoveryNode` that lifts that state machine onto the network by riding Distribution's `BroadcastNode`. After the Distribution consolidation, we own no socket of our own — every membership message is a JSON envelope inside `Message.content` on port 5678.

```python
from peer_discovery.membership_integration.service import MembershipService

service = MembershipService(room_id="default", storage_dir="/tmp/pd")
snap    = service.get_membership_snapshot()
handle  = service.subscribe_membership_events(on_event, from_version=snap.version)
```

---

## The four integrations

### 1. Distribution — already consolidated

**Status:** Wired. See [`docs/integration_contracts.md#1-distribution`](docs/integration_contracts.md#1-distribution).

- Every discovery message rides Distribution's WebSocket as a `Message` with content `{"type":"discovery_*", "sender_public_key_pem_b64":"...", "payload": {...}}`.
- We use `BroadcastNode.send_to_peer` for 1:1 (JOIN_REQUEST / JOIN_RESPONSE) and `broadcast` for 1:N (gossip / heartbeat).
- We register `DiscoveryNode.lazy_register_pubkey` on `BroadcastNode.pre_verify_hook` for trust-on-first-use.
- `MembershipRouter` (in Distribution's `distribution/membership_router.py`) consumes our snapshot + events and exposes only `ACTIVE` peers to Distribution's fan-out.
- **Open:** the `pre_verify_hook` addition should be upstreamed to Distribution's `main` branch.

### 2. Security — pubkey distribution layer

**Status:** Consolidated under the per-peer asymmetric model (Ryan's 2026-05-15 call). See [`docs/integration_contracts.md#2-security`](docs/integration_contracts.md#2-security).

- Local node's PEM comes from `initialize_private_key_store` → `chat_service.public_key_pem` → `DiscoveryConfig.public_key_override` → every outgoing discovery envelope.
- Every JOIN_ACCEPTED event carries the joiner's pubkey in `MembershipEvent.public_key`. JOIN_RESPONSE's event-log replay therefore delivers every existing peer's pubkey to a new joiner in one shot.
- Trust-on-first-use: the first envelope from a new sender plants their pubkey before `verify()` runs. We never accept a message we did not verify.
- `JOIN_REQUEST` is the **only** plaintext-signed message in the lifecycle — the joiner has no recipient pubkey yet. Closing this needs a UI step that pairs seed-IP with seed-pubkey on first connect.
- The legacy `design-message-encryption.md` v0.1 symmetric-AES-GCM model is **retired**.

### 3. History — backfill handler + auto-complete fallback

**Status:** Drop-in handler API. See [`docs/integration_contracts.md#3-history`](docs/integration_contracts.md#3-history).

- Register your replay handler via `service.register_history_handler(fn)`. We invoke it when a JOIN_ACCEPTED fires, after `start_history_backfill` has moved the joiner into BACKFILLING.
- When your replay completes, call `service.complete_history_backfill(user_id)`. We log HISTORY_BACKFILL_COMPLETE and the joiner enters ACTIVE.
- If no handler is registered (demo path), `DiscoveryNode._auto_complete_backfill_handler` immediately auto-promotes JOINING → BACKFILLING → ACTIVE.
- Distribution holds chat traffic for peers in BACKFILLING / SUSPECTED — that's the contract that lets your replay catch up before live traffic floods the joiner.

### 4. UI — `chat_service.connect(username, seed_ip)`

**Status:** Wired through `ui/services/service.py`. See [`docs/integration_contracts.md#4-ui`](docs/integration_contracts.md#4-ui).

- `seed_ip == ""` → this node is the seed (no `bootstrap_peers`).
- `seed_ip == "host"` → joiner; we default port to 5678.
- `seed_ip == "host:port"` → joiner; verbatim.
- `connect()` runs bootstrap synchronously (≤5s), so the response render sees the populated roster.
- Subscribe to `peer_registry` mutations via the membership-event handler in `service.py` to keep the user list fresh.

---

## Shared types

```python
@dataclass(frozen=True)
class MembershipEvent:
    seq_no:             int
    room_id:            str
    user_id:            str       # "host:port"
    event_type:         EventType
    timestamp:          float
    membership_version: int
    source:             str       # "local" or originator's user_id
    term:               int
    originator:         str | None
    public_key:         bytes | None
    display_name:       str
    trace_id:           str | None
```

11 `EventType` values; 7 `MemberState` values. Full schemas in `peer_discovery/membership/models.py`.

---

## Call order — joiner bootstrap

```
UI: chat_service.connect("Nitish", "10.0.0.163")
chat_service: build DiscoveryConfig + DiscoveryNode
chat_service: broadcast_node.pre_verify_hook = discover_node.lazy_register_pubkey
DiscoveryNode.start
  attempt_bootstrap
    register pending_joins[seed_addr] = Event()
    broadcast_node.send_to_peer(seed_host, seed_port, JOIN_REQUEST)
    event.wait(bootstrap_timeout=5.0s)
        # ... seed receives, plants our pubkey, applies join, sends JOIN_RESPONSE
        # ... we receive JOIN_RESPONSE, plant seed's pubkey, apply snapshot, set event
  bootstrap_success → subscribe gossip → start heartbeat threads
chat_service: render roster
```

---

## Open items

- Upstream the `pre_verify_hook` addition into Distribution's `main`.
- Close the JOIN_REQUEST-plaintext gap with a UI seed-pubkey capture step (Security + UI joint).
- Audit `docs/contract_peer_discovery.md` (Distribution's view of us) for post-consolidation accuracy.

---

## Links

- [`docs/integration_contracts.md`](docs/integration_contracts.md) — per-team contracts in depth.
- [`docs/network_layer.md`](docs/network_layer.md) — wire protocol + bootstrap + gossip.
- [`docs/membership_state_machine.md`](docs/membership_state_machine.md) — event log, snapshot, presence, durability.
- [`README.md`](README.md) — module overview.
