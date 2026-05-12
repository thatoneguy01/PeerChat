import pytest
from peer_discovery.membership_integration.notifier import EventNotifier
from peer_discovery.membership.models import MembershipEvent, EventType, MembershipDelta

def test_subscribe_and_dispatch():
    notifier = EventNotifier()
    received = []
    
    def cb(event, delta):
        received.append((event, delta))
        
    handle = notifier.subscribe(cb)
    assert notifier.subscriber_count == 1
    
    event = MembershipEvent(seq_no=1, room_id="room1", membership_version=1, event_type=EventType.JOIN_ACCEPTED, user_id="u1", source="s", term=1, timestamp=0)
    delta = MembershipDelta(type="JOIN", user_id="u1", event=event)
    
    notifier.dispatch(event, delta)
    assert len(received) == 1
    assert received[0][0] == event
    assert received[0][1] == delta
    
    notifier.unsubscribe(handle)
    assert notifier.subscriber_count == 0
    notifier.dispatch(event, delta)
    assert len(received) == 1

def test_deliver_catchup():
    notifier = EventNotifier()
    received = []
    
    def cb(event, delta):
        received.append((event, delta))
        
    handle = notifier.subscribe(cb, from_version=0)
    
    event = MembershipEvent(seq_no=1, room_id="room1", membership_version=1, event_type=EventType.JOIN_ACCEPTED, user_id="u1", source="s", term=1, timestamp=0)
    delta = MembershipDelta(type="JOIN", user_id="u1", event=event)
    
    notifier.deliver_catchup(handle, [(event, delta)])
    assert len(received) == 1
    assert received[0][0] == event
    assert received[0][1] == delta
