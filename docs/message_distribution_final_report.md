# Final Project Report Draft — Message Distribution

**Team:** Asha, Bhuvana, Shamathmika, Anukrithi, Manasa  
**Module:** Message Distribution  
**Project:** PeerChat, one-room peer-to-peer chat  
**Status:** Draft for everyone to fill in before final submission

---

## 1. Problem We Worked On

The class project is a peer-to-peer chat program with one shared chat room. Since there is no central chat server, message distribution has to do the job that a server normally does: make sure a message sent by one peer reaches every other active peer.

Our part sounds simple at first: "send the message to everyone." In practice, the hard parts were making that happen without duplicate deliveries, loops, message storms, ordering bugs, or breaking the work from Peer Discovery, History/Recovery, Security, and UI.

The main question for our team was:

> How can a peer broadcast live chat messages across a changing peer set while still giving the rest of the system simple guarantees?

The guarantees we worked toward were:

- Each live chat message should be delivered once per peer.
- Duplicate network arrivals should not become duplicate chat messages.
- Forwarding should eventually stop, even if the network graph has cycles.
- History recovery should be able to send old messages to one recovering node without flooding everyone.
- Causal metadata should move with the message so History and ordering logic can reason about missing messages.

---

## 2. High-Level Design

Message Distribution sits between the UI/History/Security layers and the network transport.

```text
UI / History / Security
        |
        v
BroadcastNode
  - duplicate suppression
  - TTL loop prevention
  - vector clock metadata
  - WebSocket send/receive
  - direct send for recovery
        |
        v
Peer connections from Peer Discovery
```

The current design uses WebSockets for peer-to-peer transport. Peer Discovery provides reachable peer addresses. Distribution uses those addresses to open connections and send either:

- **broadcast messages** for normal live chat, or
- **direct messages** for History recovery chunks.

This split became important later because History did not want to recover one node by broadcasting every old chunk to the entire network.

---

## 3. Integration Points

| Team | What Message Distribution Needs | What We Provide Back |
|---|---|---|
| Peer Discovery | A current list of reachable peers and membership events | We send messages to those peers and react when peers join/leave |
| Security | Signed/encrypted message payloads and key metadata when ready | We carry the message object over the network without changing the signed content |
| History/Recovery | A way to receive live messages and a way to send recovery chunks to one peer | `on_message` callback and `send_to_peer(host, port, message)` |
| UI | A way to submit a message and receive delivered messages | `broadcast(message)` and delivered callback path |

One thing we learned during integration is that "peer-to-peer" still needs a bootstrap story. A new node has to know at least one reachable peer or seed address. After that, Peer Discovery can share the rest of the membership list.

---

## 4. Testing and Validation Plan

We should keep this section updated as the whole class integrates.

Current validation areas:

- Unit tests for duplicate suppression, TTL behavior, direct send, and vector clock behavior.
- Integration tests with multiple local nodes using WebSockets.
- History recovery tests that send chunks through Distribution's direct send path.
- Root-level pytest so the full repo can be tested from one command.
- Manual multi-node testing on a LAN/switch before the demo.

Things to still validate together:

- Three or more real laptops connected on the same switch.
- One node temporarily disconnecting and coming back.
- Live broadcast while History recovery is happening.
- UI receiving the same message stream that History stores.
- Security signing/encryption enabled on the same message objects Distribution forwards.

---

## 5. Failures and Fixes We Should Mention

These are worth keeping in the report because the failures show what we actually learned.

| Failure / Issue | What It Taught Us | Fix / Current Status |
|---|---|---|
| Duplicate messages can arrive from more than one peer | P2P flooding is naturally redundant | Use a seen-message set before delivery/forwarding |
| A message can loop forever in a cyclic peer graph | Broadcast needs a hard stop condition | Use TTL plus duplicate suppression |
| Recovery chunks should not be broadcast to everyone | History backfill can become unnecessary network traffic | Added direct peer send path |
| Vector-clock holdback can block live messages after recovery | Recovery and live delivery share causal state | Sync vector clock after recovery completes |
| Tests passed inside a subfolder but failed from repo root | Integration tests need the same command everyone will run | Added `message-history/tests/conftest.py` |
| Different teams used slightly different assumptions | Interfaces matter as much as code | Added docs and kept API small |

---

## 6. Individual Sections

Each person can replace the placeholder text below with their work, tests, issues, and lessons learned. Try to include both what worked and what failed.

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

*Carrying Security payloads without modification.*  
Messages have a `signature` field populated by the Security team before `broadcast()` is called. Distribution forwards the message object as-is over WebSockets. It never reads, modifies, or strips the signed content, so the receiving peer gets the exact bytes that Security signed. Verification happens at the Security layer on the receiving side; Distribution's only responsibility is not to corrupt what it carries.

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
TODO

**Tests / validation:**  
TODO

**Problems found and fixes:**  
TODO

**What I learned:**  
TODO

---

### 6.3 Shamathmika

**Main work:**  
I designed and implemented the vector clock and hold-back queue that give the system causal message ordering. Without this, messages can be delivered in any order depending on network timing, which causes replies to appear before the messages they reply to.

The vector clock tracks how many messages each node has sent. Every outgoing message carries a snapshot of this clock. When a message arrives, the receiver checks two conditions before delivering it: the sender's sequence must be exactly one ahead of what the receiver has already seen from that sender, and every other node's count in the incoming clock must be at most what the receiver has seen from that node. If both pass, the message is delivered and the local clock is updated. If either fails, the message waits in the hold-back queue.

The hold-back queue buffers out-of-order messages and re-checks them after every delivery. A single delivery can cascade: delivering M1 updates the local clock, which may unblock M2, which unblocks M3, and so on. The drain loop repeats until a full pass produces nothing new.

The hold-back queue also has a 30-second timeout. If a predecessor message is permanently lost, messages waiting for it would be stuck forever. After the timeout, a stuck message is delivered out of causal order with a warning rather than held indefinitely. The timeout also merges the stuck message's clock so its successors can cascade out in the same drain pass.

I also added `sync_vector_clock` to `BroadcastNode`. When a node restarts, its vector clock is zeroed. Live messages referencing history the node missed fail the causal check and pile into hold-back. `sync_vector_clock(recovered_vc)` merges the recovered clock into the local state and drains the hold-back queue, unblocking any stuck messages. A related fix ensures the hold-back queue drains on every incoming message, not only on messages that pass the causal check, so the timeout actually fires in low-traffic conditions.

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

1. **Hold-back queue stalls permanently in low-traffic conditions.**  
   The timeout check only runs inside `drain()`, and `drain()` was only called when a message passed the causal check. If no message ever passes (for example, because the clock is zeroed after a restart), the timeout never fires and the queue is stuck indefinitely. The fix was to call `drain()` on every incoming message regardless of whether it passed the causal check.

2. **Node restarts with a zeroed vector clock, blocking all live messages.**  
   After a restart, the local clock is `{}`. Any live message with a clock like `{node1: 5}` fails `5 != 0+1` and goes into hold-back. The fix is `sync_vector_clock`, which merges the recovered clock into the local state and drains the hold-back queue in one operation.

3. **Timeout delivery creates a permanent ordering violation for late predecessors.**  
   When a timed-out message is force-delivered, the local clock advances past the gap. If the missing predecessor arrives later, it fails the sequence check and goes back into hold-back where it will time out again. This is a known limitation: one permanently lost message cascades into an ordering violation for everything behind it.

**What I learned:**  
Causal ordering is not a property of sending order, it is a property of what each node has seen. Messages can arrive out of order for normal network reasons and the system has to buffer them without stalling indefinitely. Vector clocks provide the metadata and the hold-back queue provides the mechanism, but correctness and liveness are in tension. A strict causal check is correct but can block forever if a predecessor is lost. The timeout is a deliberate tradeoff: progress is more useful than strict ordering when a message is gone for good. Making that tradeoff explicit and understanding what it costs is more valuable than avoiding the problem.

---

### 6.4 Anukrithi

**Main work:**  
I worked on the parts that I pushed to the main repo: duplicate suppression behavior, loop-prevention behavior, tests for those cases, the direct-send API that History needed for recovery chunks, and a small root-pytest fix. My focus was making sure that as messages flood through the peer network, each node processes a message only once and does not accidentally create a broadcast storm.

The first part was duplicate suppression. In a peer-to-peer broadcast, the same message can reach a node from multiple neighbors. That is normal and not automatically an error, but it becomes a bug if the node stores it twice, shows it twice in the UI, or forwards it again. I updated and tested the `BroadcastNode` behavior so a duplicate message ID is dropped before it is delivered or forwarded again.

The second part was loop prevention. Duplicate suppression handles repeated message IDs, but TTL gives us a second safety net. A message with `ttl == 0` is allowed to be delivered locally, but it is not forwarded again. When a node does forward a message, it forwards a copy with TTL decremented. I added tests for this because a small mutation bug here could make the delivered message look different from what the upper layers expected.

The third part was supporting History recovery. At first, History was using a workaround where recovery chunks could be sent through broadcast with a target-user filter. That technically works, but it wastes network traffic because every peer receives chunks that only one recovering peer needs. I added/validated `send_to_peer(host, port, message)` so History can send recovery chunks directly to one peer. The direct-send path forces `ttl=0`, which means the receiver can save the chunk locally without re-broadcasting old history to everyone.

Finally, I fixed a repo-level testing issue. Message History tests passed when run inside `message-history`, but root `pytest` failed because those tests import `storage` as a local package. I added a small `conftest.py` inside `message-history/tests` so root pytest adds the message-history folder to `sys.path`. This is not a feature change, but it matters because the whole class needs one command that tests the full repo.

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
- full root `pytest` after the conftest fix

**Problems found and fixes:**  

1. **Race risk in duplicate detection.**  
   My first concern was that duplicate suppression cannot be a loose "check first, insert later" pattern. If two copies of the same message arrive close together, both could pass the check. The fix was to make check-and-mark one locked operation.

2. **TTL mutation bug risk.**  
   If we decrement TTL on the same `Message` object that is delivered locally, the upper layers might see the wrong TTL. That is confusing for tests and can become worse once History/Security inspect message fields. The fix was to forward a copied message with `dataclasses.replace(message, ttl=message.ttl - 1)`.

3. **History recovery was too noisy if implemented as broadcast.**  
   Sending old chunks through broadcast would turn one recovering node's catch-up into traffic for every peer. The fix was a direct peer-send API. This reduces recovery traffic from "everyone sees every recovery chunk" to "only the recovering node receives its chunks."

4. **Root tests failed even though module tests passed.**  
   This was a packaging/path issue, not a logic bug. The fix was a local pytest `conftest.py` for Message History tests so root-level pytest sees the local `storage` package.

**What I learned:**  
The biggest thing I learned is that distributed bugs are often not loud. The program can keep running and still be wrong: duplicate messages can look like normal traffic, loops can look like retries, and recovery traffic can look harmless until it gets multiplied across every peer.

I also learned that the API boundary matters a lot. `broadcast()` and `send_to_peer()` seem like small differences, but they represent totally different network behavior. Broadcast is right for live chat. Direct send is right for recovery. Mixing them makes the system harder to reason about and creates unnecessary load.

I think our strongest explanation is that Message Distribution is not just "send over WebSockets." It is the layer that decides when a message is new, whether it should be forwarded, where it should go, and when it is safe to deliver.

---

### 6.5 Manasa

**Main work:**  
TODO

**Tests / validation:**  
TODO

**Problems found and fixes:**  
TODO

**What I learned:**  
TODO

---

## 7. References / Sources To Keep

We should update this list as people add their final sections.

- Leslie Lamport, "Time, Clocks, and the Ordering of Events in a Distributed System," *Communications of the ACM*, 1978.
- Colin Fidge, "Timestamps in Message-Passing Systems That Preserve the Partial Ordering," 1988.
- Python `asyncio` documentation: https://docs.python.org/3/library/asyncio.html
- Python `websockets` documentation: https://websockets.readthedocs.io/
- MDN WebSocket API overview: https://developer.mozilla.org/en-US/docs/Web/API/WebSocket
- Course lectures and class integration discussion on peer-to-peer networking, LAN/switch testing, and distribution algorithms.
