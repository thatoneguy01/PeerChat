# Peer Discovery — Integration Contracts

**Owner:** Peer Discovery (Himanshu, Ali, Abhishek)
**Status:** Current as of 2026-05-18, post-Distribution-consolidation

One document covering the four cross-team contracts: what Peer Discovery requires from Distribution, Security, History, and UI; and what we promise back. For deep technical context behind these contracts, see [`network_layer.md`](network_layer.md) and [`membership_state_machine.md`](membership_state_machine.md).

---

## What Peer Discovery is

A single public facade, `MembershipService`, that maintains room membership as an event-sourced state machine, plus a `DiscoveryNode` that lifts the in-process state machine onto the network by riding Distribution's transport.

```python
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode

service = MembershipService(room_id="default", storage_dir="/tmp/pd")
config  = DiscoveryConfig(
    advertise_address=f"{lan_ip}:5678",
    bootstrap_peers=["10.0.0.163:5678"],   # empty list = "I am the seed"
    public_key_override=our_pubkey_pem,
)
node = DiscoveryNode("default", config, storage_dir, broadcast_node=bn)
node.start(display_name="Himanshu")
```

---

## 1. Distribution

**Status:** Consolidated. Discovery owns no socket; everything rides `BroadcastNode` on port 5678.

### What we depend on (Distribution provides)

| API                                   | How we use it                                                                                       |
|---------------------------------------|-----------------------------------------------------------------------------------------------------|
| `BroadcastNode.send_to_peer(h,p,msg)` | One-to-one delivery for `JOIN_REQUEST` and `JOIN_RESPONSE` envelopes (the bootstrap path).          |
| `BroadcastNode.broadcast(msg)`        | One-to-all delivery for `discovery_gossip` and `discovery_heartbeat` envelopes.                     |
| `BroadcastNode.on_message`            | Inbound delivery slot. `chat_service.message_received` dispatches to `DiscoveryNode.handle_message` first; chat falls through if `handled == False`. |
| `BroadcastNode.pre_verify_hook`       | We assign `DiscoveryNode.lazy_register_pubkey` here so trust-on-first-use can plant a sender's pubkey before signature verification runs. Hook is exception-safe; a buggy hook can't break verify. |
| `peer_registry.add_peer(h,p,pubkey)`  | Called by our membership event handler on JOIN_ACCEPTED so Distribution learns who to fan chat out to. |
| `peer_registry.get_pub_key(h,p)`      | Read by Distribution's `_verify_incoming`; we populate it via the pre-verify hook and via JOIN_ACCEPTED. |

### What we promise back

| Promise                                 | How it's enforced                                                                                                                       |
|-----------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| Every discovery message has the literal substring `"type": "discovery_` | Distribution's chat path never routes our envelopes to the UI. `is_discovery_message(content)` in `peer_discovery/network/protocol.py:103` is the cheap sniff. |
| Every discovery envelope carries the sender's pubkey PEM (base64)       | So Distribution's pre-verify hook can lazy-register on first sight; no out-of-band key exchange. |
| `MembershipEvent` JSON shape is stable across versions                  | Backward-compatible additions only; `from_dict` is tolerant of missing optional fields.          |
| Holding traffic for `BACKFILLING` / `SUSPECTED` peers is Discovery's signal, but Distribution's responsibility | `MembershipRouter.get_peers()` returns ACTIVE only; states are visible via subscribe. |

### Call order, joiner bootstrap

```
Joiner DiscoveryNode.start
  attempt_bootstrap
    broadcast_node.send_to_peer(seed_host, seed_port, JOIN_REQUEST)
    event.wait(bootstrap_timeout)
  Seed BroadcastNode._handle_ws
    pre_verify_hook → DiscoveryNode.lazy_register_pubkey (plants joiner pubkey)
    Distribution.verify(msg) → OK
    on_message → chat_service.message_received → DiscoveryNode.handle_message
      → _handle_join_request → service.join_member
      → _send_join_response → broadcast_node.send_to_peer(joiner_host, joiner_port, JOIN_RESPONSE)
  Joiner BroadcastNode._handle_ws
    pre_verify_hook → DiscoveryNode.lazy_register_pubkey (plants seed pubkey)
    Distribution.verify(msg) → OK
    on_message → chat_service.message_received → DiscoveryNode.handle_message
      → _handle_join_response → service.apply_remote_snapshot
      → pending_joins[seed_addr].set()
  attempt_bootstrap returns True
```

---

## 2. Security

**Status:** Consolidated. Per-peer asymmetric pubkeys; group-key model retired 2026-05-15 (Ryan's call).

### What we depend on (Security provides)

| API                                         | How we use it                                                            |
|---------------------------------------------|--------------------------------------------------------------------------|
| `initialize_private_key_store(store, persistent)` | Called once in `main.py` at startup to get the local node's PEM and seed the in-memory key store. |
| `configure_private_key(priv_key)`           | Distribution uses this for `sign(msg)`. We don't sign anything ourselves; Distribution does it for our envelopes too. |
| `security.payload_encryption.encrypt_payload` | Distribution invokes this per-recipient on outgoing messages. Discovery's job ended before this; we just ensure the pubkey is in the registry. |
| `security.payload_encryption.decrypt_payload` | Distribution invokes this on inbound encrypted content. Discovery sees plaintext envelope content (post-decrypt) by the time we handle the message. |

### What we promise back

| Promise                                                                          | Why                                                                                       |
|----------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| Every peer's pubkey is distributed via discovery — either via JOIN_RESPONSE's event-log replay (for pre-existing peers) or via JOIN_ACCEPTED gossip (for new joiners) | So Security's encrypt/decrypt always finds a pubkey by the time the second message flows. |
| Trust-on-first-use is fail-secure                                                | We register the pubkey from the *same envelope it signed with* and never accept a message that fails verification. |
| The `JOIN_REQUEST` is the only plaintext-signed message in the lifecycle         | It must be plaintext because the joiner doesn't have the seed's pubkey yet. Acknowledged limitation; closing it needs a UI step that captures seed-pubkey with seed-IP. |

### What Security still owes

- The per-recipient encryption + decryption surface (`encrypt_payload` / `decrypt_payload` / `is_encrypted_content`) is **shipped** as of 2026-05-18.
- The `CryptoProvider` group-key API is **retired** — no `get_group_key`, no `set_group_key`, no `group_key_b64` on the wire.

---

## 3. History

**Status:** Drop-in handler API; auto-complete fallback for the demo path.

### What we depend on (History provides)

| API                                                | How we use it                                                                                       |
|----------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Backfill handler callable                          | History registers it via `service.register_history_handler(handler)`. We invoke it on JOIN_ACCEPTED. |
| `BroadcastNode.send_to_peer` (chat port)           | History uses Distribution's direct-send to replay chunks to one catching-up peer.                   |
| `BroadcastNode.sync_vector_clock(vc)`              | History calls this after replay completes so live messages stop piling up in hold-back.             |

### What we promise back

| Promise                                                                            | Where it lives                                                                       |
|------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| On JOIN_ACCEPTED, fire `HISTORY_BACKFILL_STARTED` → state machine enters BACKFILLING | `peer_discovery/membership/snapshot.py:24-25` allows the transition from JOINING.    |
| On `service.complete_history_backfill(user_id)`, log HISTORY_BACKFILL_COMPLETE → ACTIVE | `peer_discovery/membership_integration/service.py`; this is what History calls when done. |
| If no handler registered, auto-promote                                              | `DiscoveryNode._auto_complete_backfill_handler` (discovery_node.py:386) — the demo fallback. |
| `MembershipEvent.public_key` is populated on JOIN_ACCEPTED                          | So History's recovery sends can be signed-and-verifiable end-to-end without a separate key fetch. |

### Lifecycle diagram

```
Joiner ─JOIN_REQUEST→ Seed ─JOIN_ACCEPTED→ everyone (via gossip)
                                 │
                                 │ subscribe_membership_events handler on seed
                                 ▼
                       HISTORY_BACKFILL_STARTED (seq_no = N+1)
                                 │
                                 ▼
                       History.replay(joiner) ─send_to_peer→ Joiner
                                 │ (loops until History's chunks all delivered)
                                 ▼
                       service.complete_history_backfill(joiner_id)
                                 │
                                 ▼
                       HISTORY_BACKFILL_COMPLETE (seq_no = N+2)
                                 │
                                 ▼
                       Joiner is ACTIVE; Distribution starts fanning chat to it
```

---

## 4. UI

**Status:** Wired through `chat_service`. UI calls `connect(username, seed_ip)` and the rest is automatic.

### What we depend on (UI provides)

| API / Field                                  | How we use it                                                                       |
|----------------------------------------------|-------------------------------------------------------------------------------------|
| `chat_service.broadcast_node`                | Reference to Distribution's `BroadcastNode` instance, set by `main.py:40`.          |
| `chat_service.public_key_pem`                | The local node's pubkey PEM, set by `main.py:28`.                                   |
| `chat_service.peer_registry`                 | Distribution's `InMemoryRegistry` instance, set by `main.py:36`.                    |
| `chat_service.connect(username, seed_ip)`    | The handshake entrypoint. `seed_ip == ""` means "I am the seed"; non-empty means "join via this seed". |

### What we promise back

| Promise                                                                                                                | Where                                            |
|------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------|
| `chat_service.message_received` routes discovery envelopes to us, falls through to chat for everything else            | `ui/services/service.py:90`                      |
| Membership events are surfaced to UI as `peer_registry` mutations (peer added on JOIN_ACCEPTED, removed on LEAVE_CONFIRMED) | `ui/services/service.py:179-202`                 |
| `display_name` is propagated to UI via the membership-event handler                                                    | `user_connected(event.display_name, host)`       |
| The pre-verify hook is registered on the BroadcastNode automatically during `connect()`                                | `ui/services/service.py:161-162`                 |

### Notes on `connect(username, ip)`

- `ip == ""` constructs a `DiscoveryConfig` with no `bootstrap_peers` → seed mode.
- `ip == "10.0.0.163"` (no port) defaults the port to the chat port 5678; the seed is identified as `f"{ip}:{self.chat_port}"`.
- `ip == "10.0.0.163:5678"` (with port) is used verbatim.
- Bootstrap is synchronous and blocks the Flask request for up to 5 seconds. Returns the rendered roster in the same response.

---

## Shared types

```python
# peer_discovery/membership/models.py
@dataclass(frozen=True)
class MembershipEvent:
    seq_no: int
    room_id: str
    user_id: str            # "host:port"
    event_type: EventType
    timestamp: float
    membership_version: int
    source: str             # "local" or originator's user_id
    term: int
    trace_id: str | None
    display_name: str
    originator: str | None  # only set on events that arrived via gossip
    public_key: bytes | None  # PEM bytes; populated on JOIN_ACCEPTED
```

Eleven `EventType` values, four `MemberState` states actually exposed to Distribution (`ACTIVE`, `BACKFILLING`, `SUSPECTED`, `LEFT/DISCONNECTED`). Full set in `models.py:7-28`.

---

## Open questions and historical notes

- The legacy `NetworkMessage` codec in `peer_discovery/network/protocol.py:22-87` is retained only so older membership tests keep passing without a fixture rewrite. The wire transport for it is gone. Safe to delete once those tests are migrated.
- The 12-item changelog formerly in `UI_SERVICE_CHANGES.md` is now folded into the **What UI provides** + **What we promise back** sections above and the failures table of `peer_discovery_final_report.md`. The standalone file is retired.

---

## Links

- Network layer deep-dive: [`network_layer.md`](network_layer.md)
- Membership state machine deep-dive: [`membership_state_machine.md`](membership_state_machine.md)
- Module overview: [`../README.md`](../README.md)
- Final report: [`../../docs/peer_discovery_final_report.md`](../../docs/peer_discovery_final_report.md)
- Distribution's view of us: [`../../docs/contract_peer_discovery.md`](../../docs/contract_peer_discovery.md)
