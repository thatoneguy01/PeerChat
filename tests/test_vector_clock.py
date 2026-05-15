import asyncio
import json
import unittest

from distribution.message import Message
from distribution.vector_clock import VectorClock, HoldBackQueue
from distribution import BroadcastNode, InMemoryRegistry


def _msg(sender: str, vc: dict, content: str = "x") -> Message:
    return Message(content=content, sender=sender, vector_clock=vc)


class TestVectorClock(unittest.TestCase):

    def test_increment_starts_at_one(self):
        vc = VectorClock()
        vc.increment("A")
        self.assertEqual(vc.snapshot(), {"A": 1})

    def test_increment_accumulates(self):
        vc = VectorClock()
        vc.increment("A")
        vc.increment("A")
        self.assertEqual(vc.snapshot()["A"], 2)

    def test_increment_tracks_multiple_nodes(self):
        vc = VectorClock()
        vc.increment("A")
        vc.increment("B")
        vc.increment("A")
        snap = vc.snapshot()
        self.assertEqual(snap["A"], 2)
        self.assertEqual(snap["B"], 1)

    def test_merge_takes_element_wise_max(self):
        vc = VectorClock()
        vc.increment("A")           # A=1
        vc.merge({"A": 3, "B": 2})
        snap = vc.snapshot()
        self.assertEqual(snap["A"], 3)
        self.assertEqual(snap["B"], 2)

    def test_merge_does_not_decrease_existing_entry(self):
        vc = VectorClock()
        vc.increment("A")
        vc.increment("A")           # A=2
        vc.merge({"A": 1})
        self.assertEqual(vc.snapshot()["A"], 2)

    def test_merge_adds_new_nodes(self):
        vc = VectorClock()
        vc.merge({"B": 5})
        self.assertEqual(vc.snapshot()["B"], 5)

    def test_snapshot_returns_copy(self):
        vc = VectorClock()
        vc.increment("A")
        snap = vc.snapshot()
        snap["A"] = 999
        self.assertEqual(vc.snapshot()["A"], 1)

    # is_ready: empty vector clock

    def test_is_ready_empty_vc_always_ready(self):
        vc = VectorClock()
        self.assertTrue(vc.is_ready(_msg("A", {})))

    # is_ready: sender sequence check (condition 1)

    def test_is_ready_first_message_from_new_sender(self):
        vc = VectorClock()
        self.assertTrue(vc.is_ready(_msg("A", {"A": 1})))

    def test_is_ready_gap_in_sender_sequence_rejected(self):
        vc = VectorClock()
        # expecting A=1 but message claims A=2
        self.assertFalse(vc.is_ready(_msg("A", {"A": 2})))

    def test_is_ready_next_in_sequence_accepted(self):
        vc = VectorClock()
        vc.merge({"A": 1})
        self.assertTrue(vc.is_ready(_msg("A", {"A": 2})))

    def test_is_ready_duplicate_sender_sequence_rejected(self):
        vc = VectorClock()
        vc.merge({"A": 1})
        # VC_M[A] == VC_R[A], not +1
        self.assertFalse(vc.is_ready(_msg("A", {"A": 1})))

    # is_ready: causal predecessor check (condition 2)

    def test_is_ready_missing_causal_predecessor_rejected(self):
        vc = VectorClock()
        # B sends after seeing A=1, but local vc has A=0
        self.assertFalse(vc.is_ready(_msg("B", {"A": 1, "B": 1})))

    def test_is_ready_causal_predecessor_satisfied(self):
        vc = VectorClock()
        vc.merge({"A": 1})
        self.assertTrue(vc.is_ready(_msg("B", {"A": 1, "B": 1})))

    def test_is_ready_partial_predecessor_satisfied(self):
        vc = VectorClock()
        vc.merge({"A": 2})
        # message requires A<=2, which is satisfied
        self.assertTrue(vc.is_ready(_msg("B", {"A": 2, "B": 1})))


class TestHoldBackQueue(unittest.TestCase):

    def test_drain_releases_ready_message(self):
        vc = VectorClock()
        q = HoldBackQueue()
        msg = _msg("A", {"A": 1})
        q.add(msg)
        released = q.drain(vc)
        self.assertEqual(released, [msg])

    def test_drain_holds_unready_message(self):
        vc = VectorClock()
        q = HoldBackQueue()
        q.add(_msg("A", {"A": 2}))  # gap: A=2 but local A=0
        self.assertEqual(q.drain(vc), [])

    def test_drain_removes_released_messages(self):
        vc = VectorClock()
        q = HoldBackQueue()
        q.add(_msg("A", {"A": 1}))
        q.drain(vc)
        self.assertEqual(q.drain(vc), [])

    def test_drain_updates_vc_so_cascade_unblocks(self):
        vc = VectorClock()
        q = HoldBackQueue()
        m1 = _msg("A", {"A": 1}, "first")
        m2 = _msg("A", {"A": 2}, "second")
        m3 = _msg("A", {"A": 3}, "third")
        # add out of order
        q.add(m3)
        q.add(m2)
        q.add(m1)
        released = q.drain(vc)
        self.assertEqual(released, [m1, m2, m3])

    def test_drain_cascade_across_senders(self):
        # m2 from B requires A=1; m1 from A is added to hold-back first
        vc = VectorClock()
        q = HoldBackQueue()
        m1 = _msg("A", {"A": 1})
        m2 = _msg("B", {"A": 1, "B": 1})
        q.add(m2)
        q.add(m1)
        released = q.drain(vc)
        self.assertEqual(released, [m1, m2])

    def test_drain_leaves_still_blocked_messages(self):
        vc = VectorClock()
        q = HoldBackQueue()
        ready = _msg("A", {"A": 1})
        blocked = _msg("B", {"A": 2, "B": 1})  # requires A=2, not yet delivered
        q.add(blocked)
        q.add(ready)
        released = q.drain(vc)
        self.assertEqual(released, [ready])
        # blocked is still in the queue; drain again yields nothing
        self.assertEqual(q.drain(vc), [])


class TestBroadcastNodeCausal(unittest.TestCase):

    def _make_node(self, port: int) -> BroadcastNode:
        return BroadcastNode("127.0.0.1", port, InMemoryRegistry())

    def test_broadcast_sets_vector_clock(self):
        node = self._make_node(19001)
        msg = Message(content="hi", sender=node.address)
        asyncio.run(node._do_broadcast(msg))
        self.assertEqual(msg.vector_clock, {node.address: 1})

    def test_broadcast_increments_on_successive_calls(self):
        node = self._make_node(19002)
        m1 = Message(content="first", sender=node.address)
        m2 = Message(content="second", sender=node.address)
        asyncio.run(node._do_broadcast(m1))
        asyncio.run(node._do_broadcast(m2))
        self.assertEqual(m1.vector_clock[node.address], 1)
        self.assertEqual(m2.vector_clock[node.address], 2)

    def test_receive_holds_out_of_order_message(self):
        node = self._make_node(19003)
        delivered = []
        node.on_message = lambda msg: delivered.append(msg.content)

        sender = "127.0.0.1:9001"
        asyncio.run(node._receive(_msg(sender, {sender: 2}, "second")))
        self.assertEqual(delivered, [])

    def test_receive_delivers_in_causal_order(self):
        node = self._make_node(19004)
        delivered = []
        node.on_message = lambda msg: delivered.append(msg.content)

        sender = "127.0.0.1:9001"
        m1 = _msg(sender, {sender: 1}, "first")
        m2 = _msg(sender, {sender: 2}, "second")

        asyncio.run(node._receive(m2))      # arrives out of order, held back
        self.assertEqual(delivered, [])

        asyncio.run(node._receive(m1))      # unblocks m2 via cascade
        self.assertEqual(delivered, ["first", "second"])

    def test_receive_deduplicates_before_causal_check(self):
        node = self._make_node(19005)
        delivered = []
        node.on_message = lambda msg: delivered.append(msg.content)

        sender = "127.0.0.1:9001"
        m1 = _msg(sender, {sender: 1}, "hello")
        asyncio.run(node._receive(m1))
        asyncio.run(node._receive(m1))      # duplicate
        self.assertEqual(len(delivered), 1)

    def test_receive_empty_vc_delivered_immediately(self):
        node = self._make_node(19006)
        delivered = []
        node.on_message = lambda msg: delivered.append(msg.content)

        asyncio.run(node._receive(_msg("127.0.0.1:9001", {}, "legacy")))
        self.assertEqual(delivered, ["legacy"])

    def test_sync_vector_clock_releases_recovered_gap(self):
        node = self._make_node(19007)
        delivered = []
        node.on_message = lambda msg: delivered.append(msg.content)

        sender = "127.0.0.1:9001"
        asyncio.run(node._receive(_msg(sender, {sender: 2}, "second")))
        self.assertEqual(delivered, [])
        self.assertEqual(node.debug_state()["hold_back_count"], 1)

        released = node.sync_vector_clock({sender: 1})
        self.assertEqual(released, 1)
        self.assertEqual(delivered, ["second"])
        self.assertEqual(node.debug_state()["hold_back_count"], 0)

    def test_debug_state_exposes_held_messages(self):
        node = self._make_node(19008)
        sender = "127.0.0.1:9001"
        asyncio.run(node._receive(_msg(sender, {sender: 2}, "blocked")))

        state = node.debug_state()
        self.assertEqual(state["address"], node.address)
        self.assertEqual(state["hold_back_count"], 1)
        self.assertEqual(state["hold_back"][0]["content"], "blocked")
        self.assertEqual(state["hold_back"][0]["vector_clock"], {sender: 2})

    def test_message_serialisation_round_trip_with_vc(self):
        original = Message(content="hi", sender="A:1", vector_clock={"A:1": 3, "B:2": 1})
        restored = Message.from_json(original.to_json())
        self.assertEqual(restored.vector_clock, original.vector_clock)

    def test_message_deserialises_without_vc_field(self):
        payload = json.dumps({
            "content": "old",
            "sender": "A:1",
            "id": "abc",
            "timestamp": 0.0,
            "signature": "",
            "ttl": 10,
        })
        msg = Message.from_json(payload)
        self.assertEqual(msg.vector_clock, {})


if __name__ == "__main__":
    unittest.main()
