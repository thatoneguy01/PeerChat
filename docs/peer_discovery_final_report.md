# Final Project Report Draft — Peer Discovery

**Team:** Himanshu Jain, Mohammed Asim, Ali 

**Module:** Peer Discovery  
**Project:** PeerChat, one-room peer-to-peer chat  
**Status:** Draft for everyone to fill in before final submission

---

## 1. Problem We Worked On

The class project is a decentralized peer-to-peer chat application with one shared room and no centralized coordination server. In a traditional client-server system, a central server keeps track of connected users, distributes peer information, detects disconnects, and coordinates recovery when users reconnect. In PeerChat, these responsibilities had to be handled collaboratively by the peers themselves.

Our team's responsibility was the **Peer Discovery** layer. At first glance, peer discovery sounds simple: maintain a list of connected peers. In practice, the difficult problems were:

- allowing new peers to join an already-running network,
- synchronizing membership state across nodes,
- securely exchanging peer information,
- handling reconnects and temporary disconnects,
- integrating heartbeat and gossip systems,
- preventing stale or inconsistent membership views,
- coordinating recovery across multiple teams.

The main question for our team was:

> How can a peer securely discover, synchronize with, and stay consistent with an already-running decentralized network?

The guarantees we worked toward were:

- New peers should be able to bootstrap into the network from known peers.
- Membership state should eventually converge across nodes.
- Reconnecting peers should recover synchronized state.
- Gossip and heartbeat systems should integrate cleanly with discovery.
- Bootstrap synchronization should be secure.
- External teams should interact through stable APIs instead of internal implementation details.

---

## 2. High-Level Design

Peer Discovery sits between the networking layer and the rest of the distributed system.

```text
Message Distribution / History / Security / UI
                    |
                    v
             MembershipService
                    |
                    v
               DiscoveryNode
       ┌────────────┼────────────┐
       │            │            │
 TCPListener   GossipLayer   HeartbeatManager
       │
       v
Bootstrap / JOIN_REQUEST / JOIN_RESPONSE
```

The main orchestrator is `DiscoveryNode`, which coordinates:

- network startup,
- bootstrap joining,
- encrypted snapshot synchronization,
- gossip dissemination,
- heartbeat routing,
- membership recovery.

The design separates:

- networking,
- membership state,
- dissemination,
- failure detection,
- integration contracts.


This separation became important because different teams depended on different parts of the system. Keeping boundaries clear prevented changes in one subsystem from breaking the others.

The bootstrap process allows a new node to join an already-running network by contacting a known peer, exchanging public keys, receiving an encrypted membership snapshot, and replaying membership events locally.

The networking layer also integrates directly with:

- `MembershipService`
- `GossipDispatcher`
- `HeartbeatManager`

so the node lifecycle remains coordinated through one orchestration point.

## 3. Integration Points

| Team | What Peer Discovery Needs | What We Provide Back |
|---|---|---|
| Message Distribution | Current peer list and membership updates | Snapshot + membership event subscriptions |
| Message History | Join lifecycle and recovery coordination | BACKFILLING and ACTIVE transitions |
| Security | Public key exchange, validator hooks, and bootstrap key integration | Peer identity metadata and encrypted snapshot synchronization |
| UI | Active roster and live membership changes | Membership snapshot subscriptions |

One thing we learned during integration is that peer discovery is not just “finding peers.” It becomes the lifecycle coordinator for the entire distributed system.

The Message Distribution team depends heavily on the peer routing information exposed through membership snapshots. Distribution uses ACTIVE peer lists to determine where live messages should be forwarded and where reconnect recovery traffic should be sent.

History/Recovery depends on discovery lifecycle states because a node that is still BACKFILLING should not receive live traffic until replay synchronization is complete.

Security integrates through validator hooks and public-key exchange. Bootstrap synchronization also became part of the security boundary because membership snapshots contain sensitive topology information.

One of the later integration changes was adding explicit public-key bootstrap support. `DiscoveryNode.start()` was updated to accept an external `public_key` parameter so the Security/key-storage layer can provide the node’s local public key during startup instead of generating it internally every time.

The bootstrap flow was also updated so `attempt_bootstrap()` encodes and sends `public_key_b64` inside `JOIN_REQUEST`. This allows bootstrap peers to:
- validate peer identity,
- encrypt membership snapshots for the joining peer,
- synchronize membership state securely.

This change became important because earlier bootstrap flows had no clean integration boundary between Peer Discovery and the Security/key-storage subsystem.

The UI layer consumes membership subscriptions so active users, reconnects, disconnects, and suspected peers can be displayed live.

---

## 4. Testing and Validation Plan

We should keep this section updated as the whole class integrates.

Current validation areas:

- Bootstrap join flow
- Snapshot replay synchronization
- Gossip propagation
- Heartbeat routing
- Membership convergence
- Recovery after reconnect
- Invalid bootstrap peer handling
- Public key validation
- Multi-node synchronization

Validation methods included:

- unit tests,
- local multi-node simulations,
- integration tests,
- manual LAN testing,
- replay synchronization tests.

Things still worth validating together:

- Multiple real laptops connected on the same switch
- Simultaneous joins and reconnects
- Gossip synchronization during heavy message traffic
- Secure snapshot exchange with Security enabled
- Membership replay after temporary network partitions
- Recovery under concurrent message distribution load

One thing we learned during validation is that many distributed systems bugs do not appear in single-machine tests. Local simulations often hide:
- reconnect races,
- delayed packets,
- duplicate dissemination,
- replay ordering issues.

The LAN-based tests became significantly more valuable once networking and heartbeat systems were integrated together.

---

## 5. Failures and Fixes We Should Mention

These are worth keeping because the failures exposed real distributed systems problems instead of simple implementation bugs.

| Failure / Issue | What It Taught Us | Fix / Current Status |
|---|---|---|
| New peers started with incomplete membership state | Bootstrap requires synchronized replay | Added encrypted snapshot replay |
| Invalid bootstrap peer format crashed startup | Network input validation matters | Added host:port validation |
| Gossip and bootstrap operated independently | Discovery must coordinate dissemination | Integrated GossipDispatcher with MembershipService |
| Heartbeats existed but were disconnected from networking | Failure detection requires transport integration | Added HEARTBEAT routing |
| Duplicate replay events appeared after reconnect | Recovery requires ordered synchronization | Added membership replay handling |
| Public keys could not be serialized over JSON | Binary network data requires encoding | Added base64 public key encoding |
| Bootstrap had no clean integration with Security key storage | Discovery and Security require explicit key handoff | Added external `public_key` parameter and `public_key_b64` bootstrap exchange |
| Different teams interpreted membership states differently | Interfaces matter as much as algorithms | Added integration contracts and stable APIs |
| Reconnect timing produced false disconnects | Failure detection needs a grace period | Added SUSPECTED state |
| Snapshot synchronization exposed raw membership state | Bootstrap synchronization requires protection | Added RSA snapshot encryption |
| Local tests passed but integration tests failed | Distributed systems bugs often appear at integration boundaries | Added cross-team integration tests |

Many of these failures were discovered only after integrating with other teams. The integration process itself became one of the most valuable parts of the project because independent assumptions between modules frequently produced subtle inconsistencies.

---


## 6. Individual Sections

---

### 6.1 Mohammed Asim

**Main work:**  
I worked on the Presence & Failure Detection layer using a SWIM-inspired design. My main responsibility was implementing heartbeat-based liveness tracking and integrating PresenceManager with the networking and coordinator layers.

The system tracks:
- heartbeat timestamps,
- suspicion timing,
- disconnect transitions,
- reconnect handling.

I implemented:
- `register_member()`
- `unregister_member()`
- `record_heartbeat()`
- `check_liveness()`

The main challenge was balancing:
- fast disconnect detection,
- low false-positive rates.

A naive timeout-only system aggressively disconnected peers during temporary delays. To reduce this, I implemented a two-phase model:

```text
ALIVE
  ↓ missed heartbeats
SUSPECTED
  ↓ timeout
DISCONNECTED
```

Recovery flow:

```text
SUSPECTED
  ↓ heartbeat received
ACTIVE
```

The suspicion phase absorbs:
- temporary network delays,
- CPU pauses,
- reconnect timing,
- short packet loss

without forcing peers to fully rejoin the network.

I also integrated heartbeat routing into DiscoveryNode so HEARTBEAT messages flow through the actual transport layer instead of only existing locally.

Another important part of the work was validating that PresenceManager never mutates membership state directly. Instead, it reports:
- suspected,
- timeout,
- reconnected

through coordinator callbacks.

This separation simplified the architecture because the coordinator remains the only authoritative writer for membership state transitions.

**Tests / validation:**

- Heartbeat timeout validation
- Suspicion timing tests
- Reconnect recovery tests
- Membership replay synchronization tests
- Multi-node heartbeat integration
- Unknown peer heartbeat rejection tests

**Problems found and fixes:**

1. Immediate disconnect detection produced false positives. Temporary delays were incorrectly treated as permanent disconnects. The fix was adding the SUSPECTED state before DISCONNECTED.

2. Heartbeat logic existed independently from networking. Earlier versions had local-only heartbeat handling. The fix was integrating HEARTBEAT routing through DiscoveryNode.

3. Reconnect replay produced duplicate membership updates. Replayed events applied multiple times during reconnect recovery. The fix was version-aware replay synchronization.

4. Unknown heartbeat senders caused unnecessary processing. HEARTBEAT messages from unknown peers were initially processed like normal members. The fix was early rejection for unknown user IDs.

5. Reconnected peers sometimes re-entered ACTIVE too aggressively. The fix was validating heartbeat timing windows before state transitions.

**What I learned:**  
The biggest realization was that failure detection is mostly about balancing correctness and stability. Detecting a dead node is easy; deciding whether a node is truly dead or temporarily delayed is much harder.

I also learned that distributed timing bugs are difficult to reproduce locally. Systems that appear stable on one machine can behave very differently once real concurrency and network timing are introduced.

Another important realization was that architectural boundaries matter as much as algorithms. PresenceManager became much easier to reason about once it stopped mutating membership state directly and instead reported transitions through coordinator callbacks.

---


## 7. References / Sources To Keep

We should update this list as people add their final sections.

- Diego Ongaro and John Ousterhout, *In Search of an Understandable Consensus Algorithm (Raft)*, 2014.
- SWIM: *Scalable Weakly-consistent Infection-style Process Group Membership Protocol*.
- Apache Kafka architecture documentation.
- Apache ZooKeeper documentation.
- Amazon Dynamo paper.
- Google Dapper paper.
- Python asyncio documentation: https://docs.python.org/3/library/asyncio.html
- Python websockets documentation: https://websockets.readthedocs.io/
- Python socket programming documentation.
- Course lectures and class integration discussions on distributed systems and peer-to-peer networking.
