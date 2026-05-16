# Message Distribution — PRD / Team Plan

**Course:** SJSU CMPE 275 Enterprise Applications
**Module:** Message Distribution (part of class-wide P2P chat)
**POC:** Bhuvana
**Team (5):** Bhuvana, Asha, Manasa, Anukrithi, Shamathmika
**Delivery target:** 2026-05-13
**Drafted:** 2026-05-12

> **NOTE:** This is the original planning doc. Historical; describes intent, not the final implementation. References to `GossipNode` and TCP-sockets transport are superseded — the shipped implementation is `BroadcastNode` over WebSockets. For the current state, read `README.md`, `docs/INTEGRATION.md`, and `docs/report_section.md`.

---

## 1. Goals

1. Reliably propagate every chat message to every peer in a room, exactly once per peer.
2. Tolerate peers joining, leaving, or crashing without losing messages addressed to live peers.
3. Deliver messages in **causal order** so a reply never appears before the message it replies to.
4. Publish clear integration contracts so the other three teams (Security, Peer Discovery, History) can build against stable interfaces.
5. Ship tests and a runnable demo that prove the module works end-to-end.

## 2. Non-goals

- **Total ordering across concurrent messages.** Causal order only; concurrent messages may interleave differently at different peers. Justified in §7 of `docs/vector_clock.md`.
- **MPI transport.** Dropped — see §6 Open Questions. If the professor's written spec requires MPI, re-open this.
- **Exactly-once delivery semantics across crashes.** At-least-once with de-duplication is our bar.
- **Persistence, replay, encryption, peer discovery.** Owned by other teams; we consume their interfaces.

## 3. Scope

**In-scope (this module):**
- Gossip / epidemic broadcast
- UUID de-duplication (seen-set)
- TTL-based loop prevention
- Vector-clock causal ordering
- TCP sockets transport (primary)
- WebSockets transport (secondary)
- Integration adapters for Security, Peer Discovery, History
- Unit tests + end-to-end integration test
- Written report section

**Out-of-scope:** everything else on the whiteboard that isn't in this list.

## 4. Current state (2026-05-12)

| Component | State | Where |
|---|---|---|
| Message schema + JSON | ✅ done | `distribution/message.py` |
| Gossip broadcast | ✅ done (Asha) | `distribution/gossip_node.py` |
| UUID dedup | ✅ done | `gossip_node.py::_seen` |
| TTL loop prevention | ✅ done | `gossip_node.py::_receive`, `_forward` |
| `PeerRegistry` interface | ✅ done | `distribution/peer_registry.py` |
| TCP sockets transport | ✅ done | `_send_framed` / `_recv_framed` |
| Vector clock — design | ✅ done (Shamathmika) | `docs/vector_clock.md` |
| Vector clock — code | ⏳ in progress | TBD `distribution/vector_clock.py` |
| Dedup/loop unit tests | ⏳ pending | Anukrithi |
| WebSockets transport | ⏳ pending | Manasa |
| Integration contracts | ⏳ pending | Bhuvana |
| E2E integration test | ⏳ pending | Bhuvana |
| Report section | ⏳ pending | Bhuvana |

## 5. Assignments

| Person | Primary task | Done when |
|---|---|---|
| **Asha** | Verify broadcast still passes `demo.py`. Pair with Shamathmika to integrate vector clock into `GossipNode` per `docs/vector_clock.md` §5.3. | `demo.py` passes with VC integrated; causal-order walkthrough in §8 of vector_clock.md reproduces. |
| **Anukrithi** | Dedup + loop-prevention work; then write unit tests (see [integration_test.md](integration_test.md) §Unit tests). | `pytest` green for: duplicate UUID dropped, TTL=0 dropped, sender excluded from forwarding, seen-set thread-safe. |
| **Shamathmika** | Implement `distribution/vector_clock.py`: `VectorClock` + `HoldBackQueue` per her own doc. | Module importable; unit tests for `is_ready`, `merge`, `drain` pass in isolation (no sockets). |
| **Manasa** | WebSockets transport adapter. See [contract_transport.md](contract_transport.md). | A WS peer can exchange a message with a TCP peer via the shared `GossipNode` API. |
| **Bhuvana** | (a) Integration contracts × 3 (Security, Peer Discovery, History). (b) E2E integration test. (c) Report section. | Contracts shared with each team's POC; E2E test wires stubs of all 3 teams and passes; report section drafted for class report. |

## 6. Open questions

- **Q1 — MPI:** Is MPI transport on the professor's written requirement list, or only a whiteboard suggestion? If required, add owner; if not, the current plan drops it with justification in the report.
- **Q2 — Room model:** Single global room or multiple named rooms? `Message` currently has no `room_id`. Confirm with class before the other 3 teams hardcode assumptions.
- **Q3 — Transport interop:** Must a TCP peer talk to a WS peer, or are they separate demos? Changes Manasa's scope significantly.
- **Q4 — Security call order:** Does signature cover the vector clock field? Affects whether VC is filled before or after Security signs. Current assumption: fill VC, then sign (vector_clock.md §6).

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Other 3 teams change interface late, breaking our integration | High | Ship contracts **today**; get POC sign-off in writing. |
| Vector clock integration breaks broadcast | Medium | Asha pairs on integration, keeps `demo.py` green as regression gate. |
| WebSockets pulls in new dependencies other teams don't have | Medium | Use `websockets` library only inside the WS adapter; leave stdlib path untouched. |
| MPI dropped and professor flags it | Medium | Confirm with professor before end of day; if mandatory, scope-drop vector-clock causal ordering to make room. |
| Report not started until last minute | High | Bhuvana drafts stub section in parallel with contracts; fill in as team completes tasks. |

## 8. Timeline (24h)

| Block | Who | What |
|---|---|---|
| T+0 – T+4h | Bhuvana | Contracts × 3 drafted and shared with POCs |
| T+0 – T+6h | Shamathmika | `vector_clock.py` module + own-tests |
| T+0 – T+6h | Manasa | WebSockets adapter prototype |
| T+0 – T+4h | Anukrithi | Finish dedup/loops + unit tests |
| T+4h – T+8h | Asha + Shamathmika | Integrate VC into `GossipNode` |
| T+4h – T+8h | Bhuvana | E2E integration test harness with team stubs |
| T+8h – T+12h | All | Fix integration breaks, run E2E |
| T+12h – T+20h | Bhuvana | Report section draft; team reviews |
| T+20h – T+24h | All | Buffer / polish |

If any block overruns, the order of sacrifice is: WebSockets → vector clock causal ordering → unit-test coverage. Core gossip + dedup + TTL + report must ship.
