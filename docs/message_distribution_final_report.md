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
TODO

**Tests / validation:**  
TODO

**Problems found and fixes:**  
TODO

**What I learned:**  
TODO

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
TODO

**Tests / validation:**  
TODO

**Problems found and fixes:**  
TODO

**What I learned:**  
TODO

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
