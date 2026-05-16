# Peer Discovery & Membership Module

**SJSU CMPE 275 Enterprise Applications | Final Project**

This module implements the **Peer Discovery and Membership** component of the class's peer-to-peer distributed chat system. The service acts as the central control plane for room membership, securely coordinating state across all distributed peers in the network.

---

## How It Works

The core philosophy is **complex coordination hidden behind a small API**. The service is the single source of truth for:
- Who is currently in the room.
- Whether members are actively connected or suspected of being disconnected.
- Where members are in their join lifecycle (e.g., waiting for message history backfill).

A member goes through several states during their time in the room:
`JOINING` → `BACKFILLING` → `ACTIVE` → `LEAVING` → `LEFT`

The system is powered by an **Idempotent Event Machine** over a **Gossip Network**. If an active member fails to send heartbeats, they enter a `SUSPECTED` state (a grace period). If they recover in time by sending a heartbeat, they return to `ACTIVE` without disruption. If they do not recover before the grace period ends, they become `DISCONNECTED`.

---

## Project Structure

```text
peer_discovery/
├── membership/                 # Core membership data models and state
│   ├── event_log.py            # Append-only sequence of MembershipEvents
│   ├── models.py               # Data models (MembershipEvent, MemberState, etc.)
│   └── snapshot.py             # Materialized view of current membership
├── membership_integration/     # External facing components (Local API)
│   ├── coordinator.py          # Authoritative writer for mutations
│   ├── notifier.py             # ZooKeeper-style event subscriptions
│   └── service.py              # MembershipService — the public API facade
├── network/                    # P2P Network Layer
│   ├── bootstrap.py            # Node joining and encrypted state transfer
│   ├── crypto_provider.py      # Hybrid RSA/AES-GCM encryption
│   ├── discovery_node.py       # Distributed wrapper around the local service
│   ├── framing.py              # TCP length-prefixed framing
│   ├── gossip.py               # LRU-bounded event broadcasting
│   └── heartbeat.py            # Network liveness pings
├── docs/                       # Architecture & Integration Guides
└── README.md
```

---

## Setup & Running

**Requirements:** Python 3.11+ and `cryptography` library.

**Running a Node from CLI:**
```bash
# Launch a seed node
python -m peer_discovery.network --port 5000 --advertise 127.0.0.1:5000 --room my-room --name Seed

# Join the network
python -m peer_discovery.network --port 5001 --advertise 127.0.0.1:5001 --room my-room --bootstrap 127.0.0.1:5000 --name Node2
```

**Running the Tests:**
```bash
pytest peer_discovery/ -v
```

---

## The Network Layer

The `peer_discovery.network` package wraps the local `MembershipService` into a fully distributed P2P node (`DiscoveryNode`).

1. **Transport & Framing**: Thread-per-connection TCP server with strict 64KB framing and 30-second timeouts.
2. **Hybrid Cryptography**: All join payloads and snapshots are encrypted. A joining node provides an RSA-2048 public key, and the seed node encrypts the `SNAPSHOT_RESPONSE` using an ephemeral AES-256-GCM key wrapped by the RSA key.
3. **Gossip Protocol**: State mutations are broadcast across the network. Cyclical gossip is prevented using a 10,000-entry `seen_event_ids` LRU cache.
4. **Heartbeats**: The `HeartbeatManager` continuously pings known peers, feeding liveness data into the local `PresenceManager`.

---

## Local API Integration

The `MembershipService` facade provides local teams with an easy O(1) API.

```python
from membership_integration.service import MembershipService

# Optional: wrap with DiscoveryNode for P2P routing
service = MembershipService(room_id="my-room")

# 1. Join a member (accepts cryptographic context)
result = service.join_member(user_id="alice", display_name="Alice", public_key=b"...")

# 2. Get a fast read of current state
snapshot = service.get_membership_snapshot()

# 3. Subscribe to network events
handle = service.subscribe_membership_events(on_event)
```

*(See `team_integration_guide.md` for full instructions for the Distribution, History, and Security teams.)*

---

## Core Guarantees

| Guarantee | Reality |
|---|---|
| Single Source of Truth | Yes — The `MembershipEventLog` acts as the unquestionable append-only ledger of state changes. |
| Cryptographic Security | Yes — Joins are validated with RSA, and snapshots are encrypted with AES-GCM. |
| Deterministic State | Yes — The `MembershipSnapshot` is a materialized view derived strictly by applying the event log in order. |
| Event Notifications | Yes — Subscribers are notified sequentially of valid membership transitions via `EventNotifier` (note: synchronous dispatch, so slow callbacks block others). |
| Fast Local Reads | Yes — `get_membership_snapshot()` provides an O(1) in-memory lookup. |
| P2P Resilience | Yes — State is replicated via Gossip, and duplicate frames are safely ignored. |

---

## Team

| Member | Contribution |
|---|---|
| Himanshu | Event Log and Snapshot functionality |
| Ali | Durability, Idempotency, and core models |
| Abhishek | Coordinator, Tracing, Notifier, and public Service Facade |
| Asim | P2P Network Layer (Transport, Gossip, Bootstrap, Heartbeats, Crypto) |
