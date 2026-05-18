# Peer Discovery & Membership

**SJSU CMPE 275 Enterprise Applications | Final Project**

This module implements the **Peer Discovery & Membership** component of PeerChat, the class's peer-to-peer distributed chat system. It maintains a deterministic answer to *"who is currently in this room?"* across every peer, in real time, surviving joins, leaves, unannounced crashes, and gossip cycles.

After the Distribution consolidation, Peer Discovery owns no network socket of its own. Every membership message — JOIN, LEAVE, gossip, heartbeat — rides as a small JSON envelope inside Distribution's `BroadcastNode` on port **5678**.

---

## How It Works

A node goes through a small state machine during its time in a room:

```text
JOIN_ACCEPTED → JOINING → BACKFILLING → ACTIVE ↔ SUSPECTED → DISCONNECTED
                                          │
                                          └→ LEAVING → LEFT
```

The state machine is event-sourced. Every transition appends an immutable `MembershipEvent` to an append-only log; the current snapshot is a pure projection of that log. Two nodes that have applied the same log are guaranteed to have the same snapshot.

Cross-node consistency is achieved by **gossiping** each event to every reachable peer, deduplicated by `(originator, seq_no, event_type, user_id)`. Failure detection runs on a **SWIM-style two-phase** timer — a peer that misses several heartbeats enters `SUSPECTED`; if it stays missed past a grace period, it becomes `DISCONNECTED` and Distribution stops fanning chat to it.

For the full design of either subsystem, see:

- [`docs/network_layer.md`](docs/network_layer.md) — wire protocol, bootstrap, gossip, heartbeats, trust-on-first-use.
- [`docs/membership_state_machine.md`](docs/membership_state_machine.md) — state machine, event log, snapshot, presence, durability.
- [`docs/integration_contracts.md`](docs/integration_contracts.md) — four cross-team contracts (Distribution / Security / History / UI).

---

## Project Structure

```text
peer_discovery/
├── membership/                 # State-machine core
│   ├── event_log.py            # Append-only log — source of truth
│   ├── snapshot.py             # Materialized view + transition guards
│   ├── presence.py             # SWIM-style heartbeat liveness detector
│   ├── duplicate_guard.py      # Cross-node idempotency
│   ├── durability.py           # Checkpoint + recovery
│   ├── models.py               # MembershipEvent, MemberInfo, enums
│   └── exceptions.py
├── membership_integration/     # Public facade + writer
│   ├── coordinator.py          # Single authoritative writer
│   ├── service.py              # MembershipService — the public API
│   ├── notifier.py             # Subscribe-with-from-version
│   └── tracer.py               # Join lifecycle tracing
├── network/                    # Transport adapter (rides Distribution)
│   ├── discovery_node.py       # Dispatcher; wires us to BroadcastNode
│   ├── bootstrap.py            # Joiner-side handshake loop
│   ├── protocol.py             # Envelope codecs (4 subtypes)
│   ├── gossip.py               # Outbound gossip + dedup LRU
│   ├── heartbeat.py            # Heartbeat-out + presence-tick threads
│   ├── config.py               # DiscoveryConfig
│   └── net_utils.py            # LAN-IP autodetect
├── docs/                       # Architecture & contracts
│   ├── network_layer.md
│   ├── membership_state_machine.md
│   └── integration_contracts.md
├── team_integration_guide.md   # Short orientation for integrators
└── README.md                   # This file
```

---

## Setup

**Requirements:** Python 3.11+, the `cryptography` package, the `websockets` package (transitively required by Distribution).

```bash
pip install -r requirements.txt
```

---

## Quick Start

Peer Discovery runs as part of the full PeerChat app — there's no separate process to launch. Start the app and connect from the UI:

```bash
python3 main.py
```

Then in the browser at `http://127.0.0.1:5050`:

- Leave the seed IP blank to **create** a new room (this node becomes the seed).
- Provide a seed's `host` or `host:port` to **join** an existing room.

Two-laptop manual test (verified on 2026-05-17):

| Step | Laptop A (seed)      | Laptop B (joiner)                          |
|------|----------------------|--------------------------------------------|
| 1    | `python3 main.py`    | (wait)                                     |
| 2    | Connect, blank seed  | `python3 main.py`                          |
| 3    | (room ready)         | Connect with seed `10.0.0.163`             |
| 4    | (see B join)         | (see A in roster, receive event-log replay) |

---

## Running the Tests

```bash
python3 -m pytest peer_discovery/ -q
```

92 unit tests across `peer_discovery/membership/tests/`, `peer_discovery/membership_integration/tests/`, and `peer_discovery/network/tests/` covering the event log, snapshot guards, dedup guard, durability roundtrip, gossip dedup, bootstrap, and the two-node end-to-end through a stubbed `BroadcastNode`.

---

## Public API — `MembershipService`

```python
from peer_discovery.membership_integration.service import MembershipService

service = MembershipService(room_id="default", storage_dir="/tmp/pd")
res = service.join_member(user_id="10.0.0.16:5678", display_name="Nitish", public_key=pem_bytes)
snap = service.get_membership_snapshot()
handle = service.subscribe_membership_events(on_event, from_version=snap.version)
```

| Method                              | Purpose                                                                                                  |
|-------------------------------------|----------------------------------------------------------------------------------------------------------|
| `join_member(user_id, display_name, public_key, context=…)` | Admit a new member. Fires `JOIN_REQUESTED` / `JOIN_ACCEPTED`; sets up backfill.                          |
| `heartbeat_member(user_id)`         | Record a heartbeat. Bounces `SUSPECTED` peers back to `ALIVE` and fires `RECONNECTED`.                   |
| `start_history_backfill(user_id)` / `complete_history_backfill(user_id)` | History uses these to drive `JOINING → BACKFILLING → ACTIVE`.                                            |
| `register_history_handler(fn)`      | History plugs in their replay callback here; default is the auto-complete no-op for demos.              |
| `leave_member(user_id)`             | Voluntary departure. Fires `LEAVE_REQUESTED` / `LEAVE_CONFIRMED`.                                       |
| `apply_remote_event(event)` / `apply_remote_snapshot(events)` | Idempotently apply gossiped events / catch-up replay.                                                    |
| `get_membership_snapshot()`         | O(1) snapshot read.                                                                                     |
| `subscribe_membership_events(cb, from_version=…)` | Replay-then-live subscription. Distribution uses this via `MembershipRouter`.                            |
| `tick()`                            | Drives presence and backfill timeouts. Called by `HeartbeatManager` once per second.                    |

`DiscoveryNode` (network/discovery_node.py) is the dispatcher that lifts the service onto the wire — see [`docs/network_layer.md`](docs/network_layer.md).

---

## The Network Layer (post-consolidation)

The `peer_discovery.network` package wraps the local `MembershipService` into a fully distributed P2P node by riding Distribution's `BroadcastNode`. **No separate listener; no separate port.**

1. **Wire protocol** — four envelope subtypes carried inside `Message.content`: `discovery_join_request`, `discovery_join_response`, `discovery_gossip`, `discovery_heartbeat`. Every envelope embeds the sender's public-key PEM.
2. **Trust-on-first-use** — `DiscoveryNode.lazy_register_pubkey` is registered on `BroadcastNode.pre_verify_hook`. The first signed envelope from a new sender plants their pubkey in the registry before Distribution's `verify()` runs.
3. **Gossip** — every membership event broadcast via `BroadcastNode.broadcast`, deduplicated by `(originator, seq_no, event_type, user_id)` in a 10,000-entry LRU.
4. **Heartbeats** — one `discovery_heartbeat` envelope every 5s; presence detector decides when to fire `DISCONNECT_SUSPECTED` / `DISCONNECT_TIMEOUT`.

---

## Core Guarantees

| Guarantee                          | Reality                                                                                                              |
|------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Single source of truth             | Yes — the append-only `MembershipEventLog` is the only durable artifact; snapshot is a pure projection.              |
| Deterministic cross-node state     | Yes — same log, same snapshot. Enforced by `_ALLOWED_FROM_STATES` transition guards in `snapshot.py:20-30`.          |
| Idempotent gossip apply            | Yes — `DuplicateGuard` keyed on `(originator, seq_no, event_type, user_id)` rejects re-application.                  |
| Eventual delivery of every event   | Yes — gossip is broadcast-to-all via Distribution's ACK + retry path.                                                |
| Failure detection                  | Yes — SWIM-style two-phase, `ACTIVE → SUSPECTED → DISCONNECTED`. Thresholds configurable on `PresenceManager`.       |
| Pubkey distribution                | Yes — every JOIN_ACCEPTED carries the joiner's pubkey, and every envelope re-affirms it via trust-on-first-use.      |
| Subscriber catch-up without race   | Yes — `subscribe_membership_events(cb, from_version=…)` replays then goes live; `MembershipRouter` uses it.          |
| O(1) local read                    | Yes — `get_membership_snapshot()` is an in-memory dict lookup.                                                       |

---

## Design Decisions

| Decision                                                                | Rationale                                                                                                                  |
|-------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------|
| Consolidate transport onto Distribution's `BroadcastNode`               | One port, one signature path, one retry queue. Eliminated the entire class of "discovery port can't parse chat JSON" bugs. |
| Event-sourced state machine                                             | Determinism across nodes is automatic if every node applies the same log. Snapshot becomes a cache.                        |
| Per-peer asymmetric pubkeys (Security 2026-05-15)                       | Group-key model retired. Discovery's role narrowed to pubkey distribution; encryption is Security+Distribution.            |
| Trust-on-first-use via `pre_verify_hook`                                | Lets us bootstrap the pubkey registry from the same envelope a sender signed with — no out-of-band key exchange needed.    |
| Single-writer coordinator                                               | The only thread that mutates the log. Made dedup, durability, and notifier easy to reason about.                            |
| Bind-vs-advertise pattern (deferred)                                    | Future-proof for NAT / public-IP modes; today the bind and advertise addresses collapse to `lan_ip:5678` for the demo.     |
| Synchronous subscriber dispatch                                         | Easy ordering guarantees; documented scale ceiling.                                                                        |
| Auto-complete backfill when no History handler is registered            | Lets the demo path run without History wired in. Real History overrides via `register_history_handler`.                    |

---

## Known Limitations

- **`lan_ip` autodetect can pick the wrong NIC** on multi-interface hosts (VPN tunnel up, virtual adapter present). Symptom: `bootstrap_no_response`. Fix path is the bind-vs-advertise split.
- **`JOIN_REQUEST` is plaintext-signed** — the joiner has no recipient pubkey yet. Closing it needs a UI step that captures the seed's pubkey alongside the seed's IP.
- **One room per process.** `MembershipService.room_id` is set at construction.
- **No NAT traversal, no TLS, no mDNS.** The advertise address must be reachable peer-to-peer.
- **Synchronous subscriber callback dispatch.** A slow callback delays subsequent subscribers.

See [`docs/network_layer.md`](docs/network_layer.md) §8 and [`docs/membership_state_machine.md`](docs/membership_state_machine.md) §10 for the long versions.

---

## Team

| Member   | Contribution |
|----------|--------------|
| Himanshu | Event Log, Snapshot, and full P2P Network Layer (Transport, Gossip, Bootstrap, Heartbeats, Crypto integration) |
| Ali      | Durability, Idempotency, and core models |
| Abhishek | Coordinator, Tracing, Notifier, and public Service Facade |
