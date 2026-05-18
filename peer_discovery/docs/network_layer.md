# Peer Discovery Network Layer

**Component:** Peer Discovery
**Author:** Himanshu
**Status:** Current as of 2026-05-18, post-Distribution-consolidation

---

## 1. The Problem

The membership state machine — event log, snapshot, presence detection — was working in-process from day one. What it could not do was find other machines. Two laptops on the same LAN ran a `MembershipService` each and stayed mutually invisible: there was no transport carrying JOIN/LEAVE/HEARTBEAT events between them.

The first cut of the network layer solved that with its own TCP listener on port 8001, its own framing, and its own (RSA + AES-GCM) crypto. It worked, but it duplicated a lot of what Message Distribution had already built: WebSocket fan-out with ACK + retry, signature verification, dedup, retry queues for offline peers. We were running two parallel transports on every node, both signing the same payloads with the same keys, and both teams were chasing the same class of bug (port collisions, half-open connections, framing edge cases).

The current design consolidates onto Distribution's `BroadcastNode`. Peer Discovery does not own a listening socket. Every discovery message — `JOIN_REQUEST`, `JOIN_RESPONSE`, `GOSSIP`, `HEARTBEAT` — rides inside `Message.content` as a small JSON envelope. One port, one signature path, one retry queue.

---

## 2. The Bind-vs-Advertise Pattern

Distributed systems that need to work across LAN, NAT, and public-IP modes (Kafka, Cassandra, Consul, Postgres in clustered mode) all split the *bind* address from the *advertise* address. The bind address is what the kernel listens on (often `0.0.0.0`). The advertise address is the string every other node uses to identify and reach this node — it is what goes into the membership snapshot and what other nodes will `connect()` to.

The PeerChat network layer is structured to support that split: `DiscoveryConfig.advertise_address` is what a peer publishes, and the `BroadcastNode(host=, port=)` is what we bind to. Today `main.py:34` collapses both onto `lan_ip:5678` — the LAN-IP autodetect from `net_utils.get_lan_ip` is what every node advertises and also what it binds. That's fine for the demo (every node is on one switch), but it is the reason the May-17 two-laptop bug looked the way it did: if `get_lan_ip` chooses an interface the other laptop can't reach (multi-NIC, VPN tunnel up), the advertised address is unreachable and `JOIN_RESPONSE` never gets delivered.

Documented limitation; the bind-vs-advertise split is the upgrade path when we leave the demo network.

---

## 3. Wire Protocol

Every discovery message is a normal `distribution.Message` whose `content` field is a JSON envelope:

```json
{
  "type":   "discovery_join_request",
  "sender_public_key_pem_b64": "...",
  "payload": { "...": "..." }
}
```

Four subtypes (`peer_discovery/network/protocol.py:97-100`):

| Subtype                       | Direction       | Payload                                                       |
|-------------------------------|-----------------|---------------------------------------------------------------|
| `discovery_join_request`      | joiner → seed   | `{display_name}`                                              |
| `discovery_join_response`     | seed → joiner   | `{accepted, events, reason?}` — full event-log replay         |
| `discovery_gossip`            | any → all       | `{event}` — one `MembershipEvent` dict                        |
| `discovery_heartbeat`         | any → all       | `{}` — sender_id carries everything we need                   |

The `sender_public_key_pem_b64` field is the key to the trust-on-first-use story (Section 7) — every envelope carries its sender's pubkey so the receiver can register it before signature verification runs.

The discriminator is the literal substring check `is_discovery_message(content)` (`protocol.py:103-116`). Chat messages cannot contain it by accident because the substring is `"type": "discovery_` — chat is opaque text and would have to be quoting JSON to collide. Real validation happens in `decode_discovery_envelope`.

---

## 4. Bootstrap Protocol

The joiner does not know the seed's pubkey when it starts. It only knows the seed's address. The bootstrap is therefore the only time in the lifecycle when we deliberately ship a plaintext-signed message.

Walkthrough (joiner ↔ seed; numbers reference `peer_discovery/network/bootstrap.py` and `discovery_node.py`):

| Step | Joiner side                                                                                                | Seed side                                                                                  |
|------|------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| 1    | `attempt_bootstrap` registers a `threading.Event` keyed by the seed's `host:port` (bootstrap.py:86-88)     | —                                                                                          |
| 2    | Builds `JOIN_REQUEST` envelope with `display_name` + own pubkey; wraps it in a `Message` (bootstrap.py:91-96) | —                                                                                          |
| 3    | Calls `broadcast_node.send_to_peer(host, port, req)` (bootstrap.py:105). Fire-and-forget; bootstrap immediately blocks on the Event with `bootstrap_timeout` (5.0s by default). | —                                                                                          |
| 4    | —                                                                                                          | `_handle_ws` receives the Message. `_verify_incoming` runs the `pre_verify_hook` (`discovery_node.lazy_register_pubkey`), which extracts the joiner's pubkey from the envelope and plants it in the peer registry. |
| 5    | —                                                                                                          | Distribution's `verify()` lookup now succeeds — signature OK, ACK sent.                    |
| 6    | —                                                                                                          | `on_message` → `handle_message` → `_handle_join_request` (discovery_node.py:229). Calls `service.join_member`; coordinator fires JOIN_ACCEPTED. |
| 7    | —                                                                                                          | `_send_join_response` (discovery_node.py:344) wraps the full event-log replay + seed's pubkey into a `JOIN_RESPONSE` envelope and calls `broadcast_node.send_to_peer(joiner_host, joiner_port, response)`. By this point peer_registry holds the joiner's pubkey, so this message is **encrypted-then-signed**. |
| 8    | `_handle_ws` receives the response. `_verify_incoming` runs the pre-verify hook, plants the seed's pubkey, decrypts, verifies. | —                                                                                          |
| 9    | `on_message` → `handle_message` → `_handle_join_response` (discovery_node.py:266). Applies the snapshot via `service.apply_remote_snapshot`, then sets the pending Event. | —                                                                                          |
| 10   | `attempt_bootstrap` returns True; the bootstrap window closes.                                             | —                                                                                          |

If step 4 fails (seed unreachable, or its `lan_ip` chose the wrong NIC), the Event never fires and the joiner logs `bootstrap_no_response peer=... within 5.0s` and runs isolated — the exact failure mode we hit on the night of May 17.

---

## 5. Gossip Dissemination

Membership events flow out from the local coordinator and have to reach every other peer exactly once. The dispatcher (`peer_discovery/network/gossip.py:22`) is outbound-only and stateless beyond an LRU.

**Dedup key:** `f"{originator}:{seq_no}:{event_type}:{user_id}"`. The same key is computed by `DuplicateGuard` so an event that has been applied can never be applied twice, even after a checkpoint reload.

**Fanout policy:** every event goes to every peer via `BroadcastNode.broadcast`. We do not random-fanout; one membership event per second times a handful of peers is fully within budget and "every peer guaranteed to see every event" makes downstream reasoning easier.

**Cycle break:** events with `source == "remote"` are not re-gossiped. The originator (`source == "local"`) gossips once; receivers apply and stop. The cycle break + the dedup LRU means a gossip cycle is bounded at one hop per peer.

---

## 6. Heartbeats and Failure Detection

Heartbeats are a `discovery_heartbeat` envelope, broadcast on a wall-clock interval (5.0s default). The dispatcher is `HeartbeatManager` (`peer_discovery/network/heartbeat.py:19`), which runs two background threads:

- **`heartbeat-out`** — one envelope every `heartbeat_interval` seconds.
- **`presence-tick`** — calls `MembershipService.tick()` every `tick_interval` seconds (1.0s default). This is the clock that fires `DISCONNECT_SUSPECTED` and `DISCONNECT_TIMEOUT`. Without it, presence state never advances even if peers stop heartbeating.

Receivers route the heartbeat through `_handle_heartbeat` (`discovery_node.py:335`). Unknown senders are ignored — heartbeats from a peer who has not yet been admitted via gossip would otherwise leak state into the coordinator. Known senders update the last-heartbeat timestamp in `PresenceManager`, which is what the SWIM-style two-phase detector reads (see `peer_discovery/docs/membership_state_machine.md` for the full ACTIVE → SUSPECTED → DISCONNECTED state machine).

---

## 7. Trust-on-First-Use Pubkey Distribution

Two-phase trust:

1. **First message from a new sender** is verified by extracting the sender's pubkey *from the same envelope it signed with* and planting it in the peer registry before `verify()` runs. The hook that does this is `DiscoveryNode.lazy_register_pubkey` (`discovery_node.py:184`), registered on Distribution's `BroadcastNode.pre_verify_hook` slot (`ui/services/service.py:161-162`). The hook is idempotent — if the registry already has a key for that sender, it skips.
2. **Every subsequent message** is verified against the registered pubkey, same path Distribution uses for chat.

This is exactly the "first connect message" Ryan was asking about. We never accept a message we did not verify; we just bootstrap the lookup table from the message itself the first time. After that, the per-recipient encryption path that Distribution + Security own takes over, and discovery's role ends.

The whole pubkey-distribution responsibility lives at the discovery layer because admission events (JOIN_ACCEPTED, JOIN_RESPONSE's event-log replay) already carry pubkeys end-to-end. Putting key distribution anywhere else would mean inventing a second admission channel.

---

## 8. Limitations

- **No NAT traversal.** The advertise address must be reachable from every other peer. LAN works; LAN-via-router-with-port-forwarding works; arbitrary internet doesn't.
- **No TLS.** Distribution's WS connections are `ws://`, not `wss://`. Per-message payload encryption (Security's `payload_encryption.py`) is the only confidentiality layer we have on the wire — it's enough for the demo and the threat model the class assignment specifies, but a real deployment would terminate `wss://` at the BroadcastNode.
- **`JOIN_REQUEST` is plaintext-signed.** Section 4, step 3. Closing that hole needs a UI step that captures the seed's pubkey alongside the seed's IP — not implemented today.
- **One room per process.** `DiscoveryConfig.room_id` is set at construction and not mutable. Multi-room would mean either per-room `BroadcastNode` instances (heavy) or routing by `room_id` inside the envelope (lighter but unimplemented).
- **`lan_ip` autodetect can pick the wrong NIC** on multi-interface hosts. The May-17 two-laptop bug. Fix path is the bind-vs-advertise split (Section 2).

---

## 9. Example Walkthrough — Two-Laptop Bootstrap

Two laptops on `10.0.0.0/24`. Laptop A (seed) is `10.0.0.163`; Laptop B (joiner) is `10.0.0.16`. Both bind `BroadcastNode` on `lan_ip:5678`.

| Step | Time     | Event                                                                                       | Observed log line                                                       |
|------|----------|---------------------------------------------------------------------------------------------|-------------------------------------------------------------------------|
| 1    | T=0s     | Laptop A connects with no seed, becomes the room                                            | `bootstrap mode=SEED ... bootstrap_seed_join accepted=True`             |
| 2    | T=120s   | Laptop B connects with seed `10.0.0.163`                                                    | `bootstrap mode=JOINER peers=['10.0.0.163:5678']`                       |
| 3    | T=120s   | B sends JOIN_REQUEST (plaintext-signed, with own pubkey)                                    | `bootstrap_attempt peer=10.0.0.163:5678 ... pubkey_bytes=451`           |
| 4    | T=120.1s | A's pre_verify_hook plants B's pubkey; signature passes                                     | `lazy_register_pubkey sender=10.0.0.16:5678 pubkey_bytes=451`           |
| 5    | T=120.1s | A's coordinator accepts the join, fires JOIN_ACCEPTED                                       | `discovery_join_accepted sender=10.0.0.16:5678 seq_no=4 members_now=2`  |
| 6    | T=120.1s | A sends JOIN_RESPONSE (encrypted-to-B-then-signed)                                          | `discovery_join_response_sent to=10.0.0.16:5678 accepted=True events=6` |
| 7    | T=120.2s | B applies snapshot; bootstrap Event fires; B is ACTIVE                                      | `bootstrap_success peer=10.0.0.163:5678 members_now=2 active_now=1`     |
| 8    | T=125s   | A and B exchange heartbeats every 5s, both ways, indefinitely                               | `discovery_msg_received subtype=discovery_heartbeat ...` (alternating)  |

This is the run we captured the night of May 17 once the lan_ip detection picked the right interface on both machines.

---

## References

- `peer_discovery/network/discovery_node.py` — the main dispatcher.
- `peer_discovery/network/bootstrap.py` — joiner-side bootstrap loop.
- `peer_discovery/network/protocol.py` — envelope codecs.
- `peer_discovery/network/gossip.py` — outbound gossip + dedup LRU.
- `peer_discovery/network/heartbeat.py` — heartbeat + tick threads.
- `peer_discovery/docs/membership_state_machine.md` — the membership half of the story.
- `peer_discovery/docs/integration_contracts.md` — the four cross-team contracts.
- `distribution/broadcast_node.py` — the transport every envelope rides on.
