# Final Project Report — Message Distribution

**Team:** Asha, Bhuvana Komati, Shamathmika, Anukrithi Myadala, Manasa  
**Module:** Message Distribution  
**Project:** PeerChat, one-room peer-to-peer chat  

---

## 1. Problem We Worked On

The class project is a peer-to-peer chat program with one shared chat room. Since there is no central chat server, message distribution has to do the job that a server normally does: make sure a message sent by one peer reaches every other active peer.

Our part sounds simple at first: "send the message to everyone." In practice, the hard parts were making that happen without duplicate deliveries, loops, message storms, ordering bugs, or breaking the work from Peer Discovery, History/Recovery, Security, and UI. In a peer-to-peer network, the same message can arrive from more than one path, a peer can disappear while another peer is forwarding, and a recovery message for one node can accidentally turn into traffic for the whole room if the interface is wrong.

The main question for our team was:

> How can a peer broadcast live chat messages across a changing peer set while still giving the rest of the system simple guarantees?

The guarantees we worked toward were:

- Each live chat message should be delivered once per peer.
- Duplicate network arrivals should not become duplicate chat messages.
- Forwarding should eventually stop, even if the network graph has cycles.
- History recovery should be able to send old messages to one recovering node without flooding everyone.
- Causal metadata should move with the message so History and ordering logic can reason about missing messages.
- Invalid or unsigned messages should be rejected before the node accepts, stores, displays, or forwards them.

---

## 2. High-Level Design

Message Distribution sits between the UI/History/Security layers and the network transport. The current implementation centers on one public object, `BroadcastNode`. It is intentionally small from the outside: other modules call `broadcast(message)`, `send_to_peer(host, port, message)`, `start()`, `stop()`, and register an `on_message` callback.

```text
UI / History / Security
        |
        v
BroadcastNode
  - duplicate suppression
  - TTL loop prevention
  - vector clock metadata
  - signature verification gate
  - per-peer encrypted payloads
  - WebSocket send/receive
  - direct send for recovery
        |
        v
Peer connections from Peer Discovery
```

The current design uses WebSockets for peer-to-peer transport. Peer Discovery provides reachable peer addresses. Distribution uses those addresses to open connections and send either:

- **broadcast messages** for normal live chat, or
- **direct messages** for History recovery chunks.

This split became important later because History did not want to recover one node by broadcasting every old chunk to the entire network. Broadcast is correct for live chat because everyone in the room should see the new message. Direct send is correct for recovery because only the recovering peer needs the missing chunks.

The final send path is:

```text
UI creates Message
Distribution deduplicates local send
Security signs stable message fields
Distribution stamps vector clock
Distribution delivers locally
Distribution encrypts a peer-specific copy for each target
Distribution sends over WebSocket with ACK/retry
Receiver verifies signature before ACK/dedup/delivery/forwarding
Receiver checks vector clock ordering
Receiver delivers to UI/History and forwards if ttl > 0
```

---

## 3. Integration Points

| Team | What Message Distribution Needs | What We Provide Back |
|---|---|---|
| Peer Discovery | A current list of reachable peers, membership events, and peer public keys | We send messages to those peers and react when peers join/leave |
| Security | RSA signing/verification, private key startup, public keys from Discovery, and payload encryption helpers | We call sign/encrypt on send and verify/decrypt on receive |
| History/Recovery | A way to receive live messages and a way to send recovery chunks to one peer | `on_message` callback and `send_to_peer(host, port, message)` with `ttl=0` |
| UI | A way to submit a message and receive delivered messages | `broadcast(message)` and delivered callback path |

One thing we learned during integration is that "peer-to-peer" still needs a bootstrap story. A new node has to know at least one reachable peer or seed address. After that, Peer Discovery can share the rest of the membership list. The final app uses a seed-peer model: the first node starts, later nodes join through that known address, and then everyone learns the current members.

We also learned that there are two different ports in the integrated system:

- Peer Discovery listens on its own discovery/bootstrap port.
- Message Distribution sends live chat over the BroadcastNode chat port, currently `5678`.

Mixing those ports caused confusing protocol errors because a Distribution WebSocket client could accidentally connect to a non-Distribution service. The UI service now maps Discovery membership events to the chat port before adding peers to Distribution's registry.

---

## 4. Testing and Validation Plan

We validated the module at three levels: unit tests, local multi-node tests, and integrated app tests.

Current validation areas:

- Unit tests for duplicate suppression, TTL behavior, direct send, and vector clock behavior.
- Integration tests with multiple local nodes using WebSockets.
- History recovery tests that send chunks through Distribution's direct send path.
- Security gate tests that prove missing signatures, missing public keys, and bad signatures are rejected before ACK/dedup/delivery/forwarding.
- Payload encryption tests that prove Distribution encrypts per recipient and decrypts before UI display.
- Root-level pytest so the full repo can be tested from one command.
- Manual multi-node testing on a LAN/switch before the demo.

The latest full-repo test run after the newest commits passed:

```text
298 passed
```

---

## 5. Failures and Fixes 


| Failure / Issue | What It Taught Us | Fix / Current Status |
|---|---|---|
| Duplicate messages can arrive from more than one peer | P2P flooding is naturally redundant | Use a seen-message set before delivery/forwarding |
| A message can loop forever in a cyclic peer graph | Broadcast needs a hard stop condition | Use TTL plus duplicate suppression |
| Recovery chunks should not be broadcast to everyone | History backfill can become unnecessary network traffic | Added direct peer send path |
| Vector-clock holdback can block live messages after recovery | Recovery and live delivery share causal state | Sync vector clock after recovery completes and use a short hold-back timeout |
| Tests passed inside a subfolder but failed from repo root | Integration tests need the same command everyone will run | Added root-test path support for Message History tests |
| Different teams used slightly different assumptions | Interfaces matter as much as code | Added docs and kept API small |
| Chat was accidentally sent to a Discovery port | A reachable peer address is not enough; the service port matters | UI service maps discovery members to the Distribution chat port |
| Unsigned or missing-key messages could pass too far | Verification has to happen before ACK/dedup/delivery | Distribution now verifies before accepting or forwarding |
| Security signing key was initialized but not configured for `sign()` | Startup wiring matters as much as library code | `main.py` configures the private key after key-store startup |
| Vector-clock holdback could create a multi-minute UI delay | Strong ordering without liveness looks broken to users | Hold-back timeout reduced to 5 seconds with a background drain task |

---

## 6. Individual Sections

### 6.1 Asha

**Main work:**  
I designed and built the initial message broadcast system and the multi-node demo that validated it end to end.

The first decision was the broadcast strategy. An earlier sketch of the module used a gossip protocol that picked a random subset of peers to forward each message. That gives probabilistic coverage, not guaranteed coverage, so a peer could be skipped on a given round. For a one-room chat, every peer needs every message. I changed the strategy to broadcast-to-all: when a node originates or forwards a message, it sends to every peer in the registry concurrently. The random fanout is still in the `fanout` parameter signature for compatibility, but it is ignored.

The second decision was the transport. The original design used raw TCP with a 4-byte length prefix for framing. WebSockets are already framed and support full-duplex communication on one connection, which simplified the ACK design. Instead of maintaining a separate return connection, the receiver can send `{"ack": msg_id}` back on the same WebSocket that carried the message.

Once WebSockets were in place, I added guaranteed delivery: the sender waits up to 2 seconds for an ACK before retrying, with up to 3 attempts and a growing backoff (0.5s, 1.0s, 1.5s). If all retries fail, the failure is logged as a warning rather than an exception that stops the broadcast. This means one unreachable peer does not block delivery to all other peers.

The `BroadcastNode` is driven entirely by asyncio. Each node runs its own event loop on a daemon thread, so `broadcast(message)` and `send_to_peer(host, port, message)` are safe to call from synchronous code like a UI handler. Internally they schedule coroutines on the node's loop with `asyncio.run_coroutine_threadsafe`.

For the demo, I wrote a 10-node local simulation using ports 5001–5010. All ten nodes share one `InMemoryRegistry` so each can reach the others. One node sends a single message and the demo counts acknowledgments with a thread-safe counter. The expected output is `10/10 nodes received the message` within a few seconds. The demo also exercises causal ordering by sending messages with intentional delays between nodes and verifying they are delivered in the correct order regardless of arrival timing.

**How Message Broadcast communicates with other systems:**

`BroadcastNode` is the hub that connects all other teams. Every message that enters or leaves the system passes through it. The communication in each direction works as follows.

*Receiving peers from Peer Discovery.*  
`BroadcastNode` does not manage its own peer list. It holds a reference to a `PeerRegistry` object and calls `get_peers()` each time it needs to forward a message. For local testing, `InMemoryRegistry` (a simple in-memory list) was used. In the full application, Peer Discovery wraps its `MembershipService` in a `MembershipRouter` that implements the same `PeerRegistry` interface and returns currently reachable peers. Because the broadcast code only calls `get_peers()`, it automatically reflects whatever Peer Discovery knows at that moment — new peers are included in the next broadcast, departed peers show up as failed deliveries and are retried then warned about.

*Receiving messages from the UI.*  
When a user types a message, the UI calls `node.broadcast(message)`. That is the entire interface into Distribution from the UI side. `BroadcastNode` assigns the message a UUID, attaches a vector clock snapshot, marks it as seen, fires the local `on_message` callback so the sending node also displays it, and then forwards it to every peer.

*Delivering messages to Message History.*  
History needs every live chat message so it can persist them. It registers a listener on `node.on_message`. Every time Distribution delivers a unique message — whether originating locally or arriving from a peer — it calls that listener once. History never needs to touch the WebSocket layer; it just receives a `Message` object from the callback.

*Supporting History recovery with direct send.*  
When a node comes back online, History needs to replay old messages to it. If those chunks went through the normal `broadcast()` path, every active peer would receive every recovery chunk — one node's catch-up would become unnecessary traffic for everyone. To avoid this, I added `send_to_peer(host, port, message)`: it opens a direct WebSocket connection to exactly one peer and forces `ttl=0` on the message, so the receiver saves it locally without re-broadcasting it. History calls this for each recovery chunk, keeping catch-up traffic between just the two nodes involved.

*Re-syncing causal state after recovery.*  
After History finishes replaying old messages to a recovering node, Distribution's vector clock is still zeroed. Any live messages that arrived during recovery fail the causal readiness check (built by Shamathmika) and pile up in hold-back. `sync_vector_clock(vc)` accepts the latest clock from the recovered message set, merges it into the local state, and drains the hold-back queue. History calls this once recovery is complete. Without it, a recovered node would display old messages correctly but silently stop showing new ones.

*Carrying Security payloads safely.*
In the current integrated path, Distribution calls the Security team's signing and verification functions directly. On send, `BroadcastNode` signs the stable message fields and then prepares peer-specific encrypted copies for the wire. On receive, it verifies the signature before ACK, deduplication, delivery, or forwarding. Distribution still does not decide whether a signature is cryptographically valid itself; it owns the order and placement of those Security calls so invalid messages do not reach UI, History, or the rest of the network.

**Tests / validation:**  

- 10-node broadcast demo: one sender, nine receivers, all confirm receipt.
- Manual WebSocket tests: sent a message while one peer was offline, confirmed the retry log warning fires after three attempts without killing delivery to other peers.
- Causal ordering demo scenarios: four sequences that send messages from different nodes with racing timestamps, verified that delivery order follows send order not arrival order.
- Verified that a second send of the same message UUID produces no second delivery and no second forward (deduplication behavior).

**Problems found and fixes:**  

1. **ACK timeout fires before forwarding completes, triggering retry storms.**  
   The original `_handle_ws` sent the ACK only after `_receive()` finished. `_receive()` includes `_forward()`, which can take up to 6 seconds (3 retries × up to 2 seconds each). The upstream sender's ACK_TIMEOUT is 2 seconds, so the sender would retry before the receiver even finished forwarding. Each retry created a new forwarding task, and the tasks piled up until node3 stopped responding. The fix was to send the ACK immediately when the message arrives, then schedule `_receive()` as a background task with `asyncio.ensure_future`. The receiver confirms receipt to the sender right away and does its forwarding work independently.

2. **`python` command not found on macOS.**  
   macOS ships with Python 3 under `python3`, not `python`. The demo and tests failed until I switched everything to `python3.11` explicitly. The fix was to use `python3.11 -m pip install -r requirements.txt` and run all commands with `python3.11`.

3. **Gossip coverage is not the same as guaranteed delivery.**  
   The first version of the module used random-fanout gossip. Under light load with few nodes it appeared to work, but coverage is only probabilistic. Switching to broadcast-to-all with ACK/retry made coverage deterministic for reachable peers.

**What I learned:**  
The hardest part was not writing the code, it was understanding why simple-looking designs fail under real timing. Sending an ACK after forwarding looks like the right order (do the work, then confirm it), but in a distributed system where the sender has its own timeout clock, that sequencing creates a feedback loop. The sender's retry is not a backup, it becomes a second copy of the forwarding work, which triggers more retries from downstream peers. The fix is counterintuitive: confirm receipt immediately and do the work separately, even though that means the ACK does not reflect completed forwarding.

I also learned that a local demo with all nodes on the same machine hides real problems. Network delays, partial failures, and concurrent connections behave differently on a LAN or across machines. The 10-node local demo was enough to validate basic correctness, but the real integration tests mattered more for finding timing bugs.

---

### 6.2 Bhuvana

**Main work:**  
I worked on the Peer Discovery integration boundary, the cross-team contracts that hold our module together with the rest of the project, and the end-to-end integration test that exercises all four layers as one system.

The first part was the `MembershipRouter`. `BroadcastNode` only knows how to call `get_peers()` on a `PeerRegistry`. For local tests this is `InMemoryRegistry`, but in the integrated app the peer set is owned by the Peer Discovery team's `MembershipService`. `MembershipRouter` is the adapter that sits between them: at startup it calls `get_membership_snapshot()` to populate an internal active/hold split, then subscribes to membership events with `from_version=snapshot.version` so no event between the snapshot and the subscription is missed. From then on, every JOIN_ACCEPTED, HISTORY_BACKFILL_COMPLETE, DISCONNECT_SUSPECTED, RECONNECTED, LEAVE_CONFIRMED, and DISCONNECT_TIMEOUT event mutates the routing table without `BroadcastNode` ever needing to know about it. `get_peers()` returns only the ACTIVE peers, so we never fan out to nodes that are still backfilling or already gone.

The second part was a fix to that initialization path. The first version of `MembershipRouter` only treated BACKFILLING and SUSPECTED peers as held — JOINING peers were silently dropped on the floor. That is wrong: a JOINING peer is mid-handshake, which means we should buffer for them and then promote on backfill complete, not pretend they do not exist. The fix was a one-line change to also hold JOINING members during init, but I added four regression tests that nail down the four states explicitly so this can never regress: JOINING and BACKFILLING are held, tombstoned/LEFT members are skipped, and the node's own address is always excluded from its own routing table.

The third part was cleaning up legacy code. The original module had a `gossip_node.py` that did random-fanout TCP gossip — Asha's broadcast-to-all WebSocket design replaced it but the old file was still in the tree and four other files still mentioned `GossipNode` in comments and exports. I confirmed with Asha that the gossip path was no longer supported, deleted `gossip_node.py`, dropped the `GossipNode` export from `distribution/__init__.py`, and fixed the stale references in `message.py`, `peer_registry.py`, and `membership_router.py`. The point was to make sure new contributors did not see two competing designs in the same module and pick the wrong one.

The fourth part was the integration contracts. We had four other teams calling into Distribution and we were getting a steady stream of "what does this method actually do" questions in chat. I wrote one contract document per team in `docs/`: `contract_security.md` for sign/verify and the TTL+VC exclusion rule, `contract_peer_discovery.md` for the `MembershipRouter` lifecycle and the events we react to, `contract_history.md` for the `on_message` listener and `send_to_peer` recovery path. Each contract has a "what we provide / what we need / open questions" structure so the other team can sign off on a specific page rather than re-derive the interface from our code. `docs/INTEGRATION.md` is a one-page overview that points each consumer team at the right contract.

The most important agreement that came out of those contracts was the signing rule: Security signs `id`, `sender`, `timestamp`, and `content` only — `ttl` and `vector_clock` mutate in transit (`ttl` decrements every hop, `vector_clock` is stamped by `broadcast()` after the caller signs), so including them in the signed canonical form would invalidate the signature at every downstream peer. This is documented in `contract_security.md` and signed off there.

The fifth part was the end-to-end integration test. Unit tests exist per layer, but nothing exercised the full path UI → Security → Distribution → wire → Distribution → History → UI as a single test. I built `tests/test_integration.py` with three stubs in `tests/stubs/`: `fake_security` (sign/verify), `fake_storage` (an append-only message log), and `listeners` (a small fan-out shim so Storage and the test observer can both subscribe to one `on_message` slot). The test wires three real `BroadcastNode` instances together over real WebSockets, one node originates a signed message, and the test asserts every other node delivers it exactly once with a valid signature, every storage instance has it in its log, and unsigned messages get dropped at the verification boundary. The stubs are intentionally small and self-contained so when Security and History land their real modules, the test swaps stubs for real implementations without changing the assertion shape.

**Tests / validation:**

- `test_membership_router.py` — `test_init_peer_in_joining_state_is_held_not_invisible`, `test_init_peer_in_backfilling_state_is_held`, `test_init_skips_tombstoned_members`, `test_init_excludes_self`. These pin the four init-time peer-state behaviors so the next refactor cannot quietly drop one.
- `test_integration.py` — `test_signed_message_reaches_every_peer_once`, `test_unsigned_message_is_dropped_by_storage`, `test_duplicate_broadcast_still_delivers_once`, `test_multiple_listeners_all_see_every_message`, `test_direct_send_reaches_only_target_peer`. These run real WebSocket fan-out across three nodes and check the full pipeline behaves as the four contracts say it should.
- Verified the legacy-cleanup change did not break anything by running the full 39-test suite before and after deleting `gossip_node.py`.

**Problems found and fixes:**

1. **JOINING peers were silently dropped during MembershipRouter init.**  
   The original state filter only matched BACKFILLING and SUSPECTED. A peer mid-handshake during a snapshot would not appear in the active set and would not appear in the hold set, which meant the eventual HISTORY_BACKFILL_COMPLETE event had no entry to promote. The fix was to add JOINING to the held set, plus four regression tests that explicitly cover each init-time state.

2. **Two competing broadcast designs were present in the same module.**  
   `gossip_node.py` was a relic of an earlier random-fanout design that nobody used anymore but nothing had deleted. Confused at least one teammate during integration. The fix was to delete it, drop the export, and chase down the stale references in three other files.

3. **Other teams were re-deriving our API from our code instead of from a contract.**  
   We had repeated questions in chat about what `signature` should sign over, what listeners get called, and what happens to messages with `ttl == 0`. The fix was a contract per consumer team in `docs/`, with the signing rule (sign `{id, sender, timestamp, content}`, exclude `ttl` and `vector_clock`) called out explicitly because that one was getting wrong twice. After the contracts landed, the questions stopped.

4. **No single test exercised the full pipeline as a system.**  
   Each layer had unit tests, but nothing checked that a signed message originated by node A actually arrived signed-and-verified at the storage of node C through real WebSockets. The fix was `test_integration.py` with stub Security and Storage. When the real modules shipped, the stubs were a drop-in swap and the test still passed without changing its assertions.

**What I learned:**  
Most of my work was at the seam between teams rather than inside any one algorithm, and that turned out to be where most of the integration bugs were hiding. The unit-test layer is comfortable because every assumption is fixed; the integration layer is uncomfortable because every assumption is somebody else's code. Writing the contracts forced me to make the assumptions explicit instead of leaving them encoded in test fixtures, and once they were on paper a few of them turned out to be wrong (the JOINING bug was found while writing `contract_peer_discovery.md` — the contract said "JOINING peers are held," the code did not, and the test then existed to keep them aligned).

The other thing I learned is that adapters earn their keep. `MembershipRouter` is a small file but it is the only reason `BroadcastNode` does not need to know what a `MembershipSnapshot` is, and the only reason `MembershipService` does not need to know what a peer registry is. Putting the translation in one place meant Manasa's reconnect work and Anu's `send_to_peer` work could both happen without anyone touching the Peer Discovery side of the boundary.

---

### 6.3 Shamathmika

**Main work:**  
I designed and implemented the vector clock and hold-back queue that give the system causal message ordering. Without this, messages can be delivered in any order depending on network timing, which causes replies to appear before the messages they reply to.

The vector clock tracks how many messages each node has sent. Every outgoing message carries a snapshot of this clock. When a message arrives, the receiver checks two conditions before delivering it: the sender's sequence must be exactly one ahead of what the receiver has already seen from that sender, and every other node's count in the incoming clock must be at most what the receiver has seen from that node. If both pass, the message is delivered and the local clock is updated. If either fails, the message waits in the hold-back queue.

The hold-back queue buffers out-of-order messages and re-checks them after every delivery. A single delivery can cascade: delivering M1 updates the local clock, which may unblock M2, which unblocks M3, and so on. The drain loop repeats until a full pass produces nothing new.

The hold-back queue has a 5-second timeout. If a predecessor message is permanently lost, messages waiting for it would be stuck forever. After the timeout, a stuck message is delivered out of causal order with a warning rather than held indefinitely. The timeout also merges the stuck message's clock so its successors can cascade out in the same drain pass.

To ensure the timeout fires regardless of traffic, drain is called by a background coroutine (`_holdback_drain_task`) that runs every 2 seconds inside the asyncio event loop. Without this, drain only runs when a new message arrives. In a quiet network, a buffered message could remain past its timeout indefinitely because nothing triggers a re-check.

I also added `sync_vector_clock` to `BroadcastNode`. When a node restarts, its vector clock is zeroed. Live messages referencing history the node missed fail the causal check and pile into hold-back. `sync_vector_clock(recovered_vc)` merges the recovered clock into the local state and drains the hold-back queue, unblocking any stuck messages.

**Tests / validation:**  
Tests cover the full vector clock and hold-back behavior:

- `test_increment_starts_at_one`, `test_increment_accumulates`, `test_increment_tracks_multiple_nodes`: basic clock increment behavior
- `test_merge_takes_element_wise_max`, `test_merge_does_not_decrease_existing_entry`, `test_merge_adds_new_nodes`: merge correctness
- `test_snapshot_returns_copy`: snapshot isolation so callers cannot mutate internal state
- `test_is_ready_*`: seven tests covering the causal readiness check including gaps, duplicates, missing predecessors, and partial satisfaction
- `test_drain_releases_ready_message`, `test_drain_holds_unready_message`, `test_drain_removes_released_messages`: basic drain behavior
- `test_drain_updates_vc_so_cascade_unblocks`, `test_drain_cascade_across_senders`: cascade delivery across single and multiple senders
- `test_drain_leaves_still_blocked_messages`: verifies partially unblocked queues leave the rest intact
- `test_drain_timeout_delivers_stuck_message_out_of_order`, `test_drain_timeout_advances_vc_so_successor_is_released`: timeout delivery and its cascade effect
- `test_broadcast_sets_vector_clock`, `test_broadcast_increments_on_successive_calls`: outgoing messages carry correct clocks
- `test_receive_holds_out_of_order_message`, `test_receive_delivers_in_causal_order`: end-to-end causal ordering through BroadcastNode
- `test_receive_deduplicates_before_causal_check`, `test_receive_empty_vc_delivered_immediately`: edge cases
- `test_sync_vector_clock_unblocks_held_messages`, `test_sync_vector_clock_is_idempotent`: recovery sync correctness
- `test_message_serialisation_round_trip_with_vc`, `test_message_deserialises_without_vc_field`: backwards-compatible serialization

**Problems found and fixes:**  

1. **Hold-back timeout does not fire in low-traffic conditions.**  
   The timeout check runs inside `drain()`, but `drain()` was only called when a new message arrived and passed the causal check. In a quiet network where traffic stops after a message is buffered, the timeout never fires and the queue stalls indefinitely. The fix was a background coroutine that calls `drain()` every 2 seconds so the timeout fires based on wall time rather than on incoming traffic.

2. **Node restarts with a zeroed vector clock, blocking all live messages.**  
   After a restart, the local clock is `{}`. Any live message with a clock like `{node1: 5}` fails `5 != 0+1` and goes into hold-back. The fix is `sync_vector_clock`, which merges the recovered clock into the local state and drains the hold-back queue in one operation.

3. **Timeout delivery creates a permanent ordering violation for late predecessors.**  
   When a timed-out message is force-delivered, the local clock advances past the gap. If the missing predecessor arrives later, it fails the sequence check and goes into hold-back again where it will time out again. One permanently lost message cascades into an ordering violation for everything behind it. This is a known limitation of timeout-based approaches to causal ordering.

**What I learned:**  
The most significant realization was that correctness and liveness require separate mechanisms. The causal readiness check is sufficient to guarantee ordering under normal conditions, but it provides no bound on how long a message can wait. The timeout addresses liveness, and the background drain task makes the timeout meaningful in practice rather than only in theory. These are distinct design concerns, and conflating them leads to implementations that appear correct in unit tests but fail under realistic traffic patterns.

Working with other teams also illustrated that module-level correctness does not imply system-level correctness. Some failure modes only emerge when two independently correct components interact in a way that neither team's tests would exercise. This reinforced the importance of integration testing across module boundaries, not just within individual components.

---

### 6.4 Anukrithi

**Main work:**  
My work was mostly in the "make the broadcast path safe" category. I pushed the duplicate-suppression and loop-prevention tests, helped add the direct-send API that History needed for recovery chunks, fixed a root-level pytest issue, and later wired two Security checks into the Distribution path. The common theme was making sure a message can move through a noisy peer network without being processed twice, forwarded forever, or accepted when it fails the Security contract.

The deduplication work came from a very basic P2P problem: the same message can reach a node from multiple neighbors. That is normal network behavior, not automatically an error. It only becomes a bug when the node stores it twice, shows it twice in the UI, or forwards it again. I tested and helped lock down the `BroadcastNode` behavior so each node treats the message UUID as the identity of the message. Once a UUID is seen, later arrivals with the same ID are dropped before delivery or forwarding.

Loop prevention was the other half of that same problem. Deduplication stops repeated processing, but TTL gives the network a hard stop even if the peer graph has cycles or a bug causes repeated forwarding attempts. A message with `ttl == 0` can still be delivered locally, but it does not get forwarded again. When a node forwards a message, it forwards a copy with the TTL decremented instead of mutating the object that was delivered locally. I added tests around this because a small object-mutation mistake here would be hard to notice during a manual demo but could confuse History or Security later.

The History integration was where the difference between "broadcast" and "send" became really important. The initial recovery workaround was basically to broadcast old chunks with a target-user filter. That technically lets the right peer ignore or accept the right data, but it still makes every peer receive recovery traffic that only one node asked for. I added and validated `send_to_peer(host, port, message)` so History can send recovery chunks directly to one peer. That path forces `ttl=0`, so the receiver can save the chunk locally without re-broadcasting old history to the room.

I also fixed a boring but important testing problem. Message History tests passed when run from their own folder, but root `pytest` failed because those tests needed their local package path. I added the small pytest path fix so the whole class can run the full repo from the root instead of remembering per-team test commands. It is not a feature change, but it matters because integration testing only works if everyone can run the same command.

After the Security team added RSA signatures, I updated Distribution so it enforces the contract at the right place in the receive path. Verification now happens before ACK, deduplication, delivery, or forwarding. That order matters more than I expected at first: if we ACK first, the sender thinks the message was accepted; if we dedup first, an invalid message can poison the seen-message set; if we deliver first, UI or History can store bad data. The current behavior is to return a NACK and drop the message when the signature is missing, the sender public key is missing, or verification fails.

One more integration bug showed up on the outgoing side. The key store was being initialized, but the Security module's signing function still needed the private key configured at startup. I updated `main.py` so after the private key is loaded/generated, it calls `configure_private_key(...)`. That made outgoing messages actually sign instead of relying on the key existing somewhere else in the app.

**Tests / validation:**  
I added and ran tests around the behavior I changed:

- `test_deduplicate_returns_true_once_false_after_that`
- `test_receive_processes_duplicate_only_once`
- `test_receive_ttl_zero_delivers_but_does_not_forward`
- `test_receive_does_not_mutate_delivered_message_ttl`
- `test_local_duplicate_broadcast_is_not_delivered_or_forwarded_twice`
- `test_direct_send_targets_one_peer_and_forces_ttl_zero`
- `test_direct_send_does_not_use_broadcast_forwarding`
- integration coverage that checks direct send reaches only the target peer
- reconnect regression coverage for queued direct sends
- security-gate tests for unsigned, missing-key, and tampered messages
- key-bootstrap test proving the configured private key can sign and verify a message
- full root `pytest` after the conftest fix and after the later integration changes

**Problems found and fixes:**  

1. **Race risk in duplicate detection.**  
   My first concern was that duplicate suppression cannot be a loose "check first, insert later" pattern. If two copies of the same message arrive close together, both could pass the check. The fix was to make check-and-mark one locked operation.

2. **TTL mutation bug risk.**  
   If we decrement TTL on the same `Message` object that is delivered locally, the upper layers might see the wrong TTL. That is confusing for tests and can become worse once History/Security inspect message fields. The fix was to forward a copied message with `dataclasses.replace(message, ttl=message.ttl - 1)`.

3. **History recovery was too noisy if implemented as broadcast.**  
   Sending old chunks through broadcast would turn one recovering node's catch-up into traffic for every peer. The fix was a direct peer-send API. This reduces recovery traffic from "everyone sees every recovery chunk" to "only the recovering node receives its chunks."

4. **Root tests failed even though module tests passed.**  
   This was a packaging/path issue, not a logic bug. The fix was a local pytest `conftest.py` for Message History tests so root-level pytest sees the local `storage` package.

5. **Invalid signed-message state could still affect Distribution.**  
   Security correctly provided signing and verification, but Distribution still had to decide where that check belongs. The first instinct is to verify near delivery, but that is too late. The fix was to verify right after parsing the WebSocket frame and before ACK/dedup/delivery/forward. That way an invalid message cannot be acknowledged, cannot enter the duplicate set, cannot be stored by History, and cannot be forwarded to other peers.

6. **Signing key existed but was not connected to the runtime sign path.**  
   The app created or loaded a key pair at startup, but `security.sign(msg)` could still fail if the module-level private key was not configured. The fix was to wire `configure_private_key(key_store.get_private_key())` in `main.py` immediately after key initialization.

**What I learned:**  
The biggest thing I learned is that distributed bugs are often not loud. The program can keep running and still be wrong: duplicate messages can look like normal traffic, loops can look like retries, and recovery traffic can look harmless until it gets multiplied across every peer.

I also learned that the API boundary matters a lot. `broadcast()` and `send_to_peer()` seem like small differences, but they represent totally different network behavior. Broadcast is right for live chat. Direct send is right for recovery. Mixing them makes the system harder to reason about and creates unnecessary load.

The Security fixes taught me the same lesson from another angle: having a good `verify()` function is not enough unless Distribution calls it at the right time. The order of operations is part of the correctness story. For this project, the correct order is verify first, then ACK, dedup, deliver, and forward.

I think our strongest explanation is that Message Distribution is not just "send over WebSockets." It is the layer that decides when a message is new, whether it should be forwarded, where it should go, and when it is safe to deliver.

---

### 6.5 Manasa

Main work:
I worked on reconnect readiness and recovery reliability for Message Distribution. My focus was making sure a peer that is temporarily offline can come back without losing messages and without needing the whole system to restart.

I helped validate the WebSocket startup/shutdown behavior, especially that a node can stop and restart on the same port cleanly. This matters because in a real demo or LAN test, a peer may disconnect, restart, and rejoin using the same address. I also worked with the hello/hello_ack handshake path so a node can confirm that another peer is reachable in both directions before treating it as ready.

Another part of my work was the retry queue for failed sends. If a peer is offline, `BroadcastNode` should not crash or block the rest of the broadcast. Instead, failed messages are queued for that peer and retried later. When the peer comes back and the handshake succeeds, the queued messages are flushed. This is especially useful for direct recovery sends, because History may need to replay chunks to a node that is not ready yet.

Tests / validation:
I validated this behavior with reconnect-focused tests:
  * `test_stop_releases_port_so_node_can_restart_on_same_port`
  * `test_hello_probe_confirms_two_way_websocket_path`
  * `test_failed_direct_send_is_queued_and_flushed_after_peer_returns`

These tests check that the server releases its port after stopping, the hello probe returns the correct `hello_ack`, and a failed direct send is saved in the retry queue, then delivered once the target peer starts again.

Problems found and fixes:
  1. Node restart could fail if the old WebSocket server did not release the port cleanly.
The fix was to make `stop()` signal the event loop, wait for the server thread to finish, and clear the loop/thread state so a new node can bind to the same port.

  2. A peer being listed in the registry does not always mean it is actually reachable.
The fix was to use a lightweight hello/hello_ack handshake. This gives Distribution a simple readiness check instead of assuming every listed peer is currently online.

  3. Direct recovery messages could be lost if the target peer was offline.
The fix was to queue failed sends by peer address and flush that queue after the peer reconnects successfully.

What I learned:
I learned that reconnect logic is an important part of message distribution, not just an edge case. In a peer-to-peer system, peers can leave, restart, or temporarily fail at any time. The system should continue delivering to reachable peers while keeping failed sends recoverable. I also learned that “peer exists” and “peer is ready” are different states, and the handshake/retry queue helped make that distinction clear.

## 7. References

These sources connect directly to the design choices in the module: causal ordering, WebSocket transport, asyncio concurrency, and signature/encryption behavior.

- Leslie Lamport, "Time, Clocks, and the Ordering of Events in a Distributed System," *Communications of the ACM*, 1978.
- Colin Fidge, "Timestamps in Message-Passing Systems That Preserve the Partial Ordering," 1988.
- Python `asyncio` documentation: https://docs.python.org/3/library/asyncio.html
- Python `websockets` documentation: https://websockets.readthedocs.io/
- MDN WebSocket API overview: https://developer.mozilla.org/en-US/docs/Web/API/WebSocket
- RFC 6455, "The WebSocket Protocol": https://www.rfc-editor.org/rfc/rfc6455
- RFC 8017, "PKCS #1: RSA Cryptography Specifications Version 2.2": https://www.rfc-editor.org/rfc/rfc8017
- NIST SP 800-38D, "Recommendation for Block Cipher Modes of Operation: Galois/Counter Mode (GCM) and GMAC": https://csrc.nist.gov/pubs/sp/800/38/d/final
- Course lectures and class integration discussion on peer-to-peer networking, LAN/switch testing, and distribution algorithms.
