# Final Project Report — Peer Discovery

**Team:** Himanshu, Ali,Asim Mohammed, Abhishek
**Module:** Peer Discovery & Membership
**Project:** PeerChat — one-room peer-to-peer chat
**Status:** Final, 2026-05-18

---

## 1. Problem We Worked On

In a peer-to-peer chat with no central server, every peer needs to maintain its own answer to *"who is currently in this room?"* — and every peer's answer has to agree, in real time, surviving joins, leaves, unannounced crashes, and gossip cycles. Without that, every other module breaks: Distribution doesn't know who to fan messages out to, History doesn't know whose backlog needs replay, the UI shows a stale roster, and Security has no way to know whose pubkey to encrypt to.

The questions our module had to answer were:

> *How does a new node discover an existing room, securely?*
> *How do every peer's view of room membership stay consistent, despite redundant gossip and unannounced failures?*
> *How do we detect that a peer has gone away without it telling us?*
> *How do we deliver an admission decision to the rest of the room without a central authority?*

The guarantees we worked toward were:

- A new node can join a room by knowing only one seed's address, and end up with a complete view of every other member, including their public keys.
- Every membership event (join, leave, suspect, timeout, reconnect) reaches every peer exactly once, idempotently — duplicate gossip is silently dropped.
- A peer that stops heartbeating is moved out of the active fan-out set on a timer, not on an announcement.
- Restart is cheap: a returning node catches up from a checkpoint instead of replaying the entire log from genesis.
- The membership snapshot at any node is a deterministic function of the event log; given the same log, two nodes always produce the same snapshot.

---

## 2. High-Level Design

Peer Discovery sits between the UI/Security layers and the network transport. After the Distribution consolidation, our network layer is an adapter: it borrows Distribution's `BroadcastNode` instead of running its own TCP server.

```text
                UI / Security
                     │
                     ▼
            MembershipService (facade)
            ┌─────────┴──────────┐
            │                    │
    EventLog (append-only)   Coordinator (single writer)
            │                    │
            ▼                    ▼
       Snapshot ─────────► Notifier (subscribe-with-from-version)
       (projection)                │
                                   ▼
                        ┌──── Distribution ────┐
                        │  BroadcastNode       │
                        │  - pre_verify_hook   │
                        │  - on_message        │
                        │  - send_to_peer      │
                        │  - broadcast         │
                        └──────────────────────┘
                                   ▲
                                   │
                              Peer connections
```

The control plane (event log → coordinator → snapshot → notifier) is in-process and single-writer. The wire plane (`DiscoveryNode` + gossip + heartbeat) lifts the local state machine onto the network by serializing every event into a JSON envelope inside `Message.content` and handing it to Distribution.

For the full design of each subsystem:
- Wire protocol, bootstrap, gossip, heartbeats, trust-on-first-use → [`peer_discovery/docs/network_layer.md`](../peer_discovery/docs/network_layer.md).
- State machine, event log, snapshot, presence detector, durability → [`peer_discovery/docs/membership_state_machine.md`](../peer_discovery/docs/membership_state_machine.md).

---

## 3. Integration Points

| Team           | What Peer Discovery Needs from Them                                                                                  | What We Provide Back                                                                                                                |
|----------------|----------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| Distribution   | `BroadcastNode.send_to_peer`, `broadcast`, `on_message`, `pre_verify_hook`, `peer_registry.add_peer`/`get_pub_key`   | Wire envelopes that route deterministically (`is_discovery_message`); a `MembershipRouter` that returns only `ACTIVE` peers for fan-out; an exception-safe `pre_verify_hook` implementation. |
| Security       | `sign` / `verify` via Distribution; `encrypt_payload` / `decrypt_payload` per-recipient; one source-of-truth pubkey PEM | Pubkey distribution at admission time (JOIN_ACCEPTED carries `public_key`); trust-on-first-use registration on every envelope.       |
| History        | A replay callable registered via `service.register_history_handler`                                                  | `HISTORY_BACKFILL_STARTED` / `HISTORY_BACKFILL_COMPLETE` events that gate live chat to a joining peer; auto-complete fallback for demos. |
| UI             | `chat_service.connect(username, seed_ip)` semantics, `peer_registry` reference, public_key_pem                       | Membership events surfaced as user-list mutations, synchronous bootstrap that completes within 5s.                                  |

Detailed per-team contracts: [`peer_discovery/docs/integration_contracts.md`](../peer_discovery/docs/integration_contracts.md).

---

## 4. Testing and Validation

**Automated unit + integration tests.** 92 tests across three suites:

- `peer_discovery/membership/tests/` — event log append, snapshot projection, transition guards, dedup guard, durability roundtrip.
- `peer_discovery/membership_integration/tests/` — coordinator single-writer discipline, notifier replay-then-live semantics, service facade end-to-end.
- `peer_discovery/network/tests/` — protocol codec roundtrip, gossip dispatcher LRU dedup, heartbeat scheduling, two-node end-to-end through a stub `BroadcastNode`.

Run with `python3 -m pytest peer_discovery/ -q`.

**Manual two-laptop validation.** Verified on 2026-05-17 across two real machines on `10.0.0.0/24`:

- Seed boots cleanly with no `bootstrap_peers`, becomes a one-member room.
- Joiner connects with seed's IP only (no pubkey); JOIN_REQUEST plaintext-signed; pre_verify_hook plants the joiner's pubkey on the seed; JOIN_RESPONSE returns encrypted-then-signed with a full 6-event log replay; the joiner enters ACTIVE within ~200ms.
- Bidirectional heartbeats sustained for 30+ minutes without a single drop.
- Live chat flows both directions, signed and verified end-to-end.

**End-to-end E2E backup test.** `test_backend_e2e.py` exercises the full Distribution+Discovery integration with two stubbed `BroadcastNode` instances communicating in-process, verifying the JOIN handshake, gossip propagation, and the trust-on-first-use bootstrap path without needing real WebSockets.

---

## 5. Failures and Fixes

These are worth keeping in the report because the failures show what we actually learned.

| Failure / Issue                                                                                                       | What It Taught Us                                                                                              | Fix / Current Status                                                                                                                            |
|-----------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| Two laptops on the same LAN couldn't find each other — discovery owned its own port, but Distribution had its own too | "P2P chat" still needs a transport story; running two parallel transports doubles the failure surface          | Consolidated onto Distribution's `BroadcastNode`. One port, one signature path, one retry queue.                                                |
| Chat messages sent to the discovery port produced "Incoming frame size 1195725856" (= ASCII `GET `) errors            | Two transports on different ports is a configuration time bomb; nodes will inevitably target the wrong one     | After consolidation there is one chat port; no way to send to the wrong place.                                                                  |
| `JOIN_RESPONSE` arrived but Distribution dropped it for "missing sender public key"                                   | Trust bootstrap and signature verification must agree on ordering — register-before-verify, not the other way  | Added `pre_verify_hook` to Distribution's `_verify_incoming`. `lazy_register_pubkey` plants the sender's pubkey from the same envelope it signed with. |
| Security design doc (v0.1) specified symmetric AES-256-GCM with a shared group key — contradicted Ryan's verbal spec  | Verbal updates can outpace the design doc; trust the conversation, but capture the decision in writing the same day | Confirmed asymmetric per-peer pubkeys; group-key surface fully removed; design doc retired; this report and the integration contracts capture the new model. |
| Validator hook framing (inferred from team_integration_guide.md) was a phantom requirement — never in Security's spec | Don't invent contracts from one-line mentions in someone else's docs; verify before implementing               | Dropped the validator approach entirely. Single source of truth for the pubkey is now `chat_service.public_key_pem` propagated through `DiscoveryConfig.public_key_override`. |
| Joiner bootstrap reported `bootstrap_no_response peer=10.0.0.163:5678` even with firewalls disabled                   | `getsockname` against `8.8.8.8` returns *an* IP, not necessarily the *reachable* IP; multi-NIC laptops misroute | Added hostname-resolution fallback in `net_utils.get_lan_ip` for isolated LANs. Long-term fix is the bind-vs-advertise split (documented limitation). |
| Heartbeat threads ran but presence state never advanced from SUSPECTED back to ACTIVE                                 | The presence detector is a clock, not a callback — without a tick, nothing fires                               | Added `presence-tick` thread in `HeartbeatManager` calling `MembershipService.tick()` every 1s.                                                  |
| 12 separate ui/services/service.py changes accumulated as an undated changelog in `UI_SERVICE_CHANGES.md`             | Per-fix changelogs rot fast; reference docs need to be the durable artifact                                    | Retired the changelog file. Architectural items folded into the integration contract; transient items captured in this table.                  |
| Direct-send to a known-but-not-yet-reachable joiner could be lost                                                     | "Peer exists" and "peer is ready" are different states; the registry tells us the former, not the latter        | We rely on Distribution's retry queue (`_send_with_retry` + `_flush_peer_retry_queue`) to flush queued sends once the handshake completes.       |

---

## 6. Limitations Still on the Table

- **`get_lan_ip` heuristic.** UDP-trick + hostname fallback works on the demo network; multi-NIC hosts can still misroute. Real fix is the bind-vs-advertise split.
- **JOIN_REQUEST is plaintext-signed.** Acceptable v1; closing it needs the UI to pair seed-IP with seed-pubkey on first connect.
- **One room per process.** `MembershipService.room_id` is constructor-fixed.
- **No NAT traversal, no TLS, no mDNS.** The advertise address must be reachable peer-to-peer.
- **Synchronous subscriber dispatch.** Slow callbacks delay every later subscriber.

Per-subsystem detail: [`peer_discovery/docs/network_layer.md`](../peer_discovery/docs/network_layer.md) §8 and [`peer_discovery/docs/membership_state_machine.md`](../peer_discovery/docs/membership_state_machine.md) §10.

---

## 7. References

- Das, A., Gupta, I., and Motivala, A. *SWIM: Scalable Weakly-consistent Infection-style Process Group Membership Protocol*, DSN 2002. (The two-phase failure detector borrowed for `PresenceManager`.)
- Lamport, L. *Time, Clocks, and the Ordering of Events in a Distributed System*, CACM 1978. (Logical-clock thinking that informed `seq_no` + `originator` keying.)
- Kreps, J. *The Log: What every software engineer should know about real-time data's unifying abstraction*, 2013. (Event-sourcing + materialized-view approach we followed for log/snapshot separation.)
- Python `asyncio` documentation — used indirectly via Distribution's `BroadcastNode`.
- Python `threading` and `queue` documentation — used by the coordinator's RLock and the heartbeat threads.
- Course lectures on peer-to-peer systems, gossip protocols, and failure detection.

---

## Appendix — Module-internal documentation

- [`peer_discovery/README.md`](../peer_discovery/README.md) — module overview, project structure, public API, design decisions.
- [`peer_discovery/team_integration_guide.md`](../peer_discovery/team_integration_guide.md) — one-page orientation for integrators.
- [`peer_discovery/docs/network_layer.md`](../peer_discovery/docs/network_layer.md) — wire protocol deep-dive.
- [`peer_discovery/docs/membership_state_machine.md`](../peer_discovery/docs/membership_state_machine.md) — state machine deep-dive.
- [`peer_discovery/docs/integration_contracts.md`](../peer_discovery/docs/integration_contracts.md) — per-team contracts.
