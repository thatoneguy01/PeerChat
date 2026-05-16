import time
import logging
from peer_discovery.membership_integration.service import MembershipService

# Set up basic logging to see the scheduler in action
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("Demo")

def run_demo():
    print("=" * 60)
    print("[ MEMBERSHIP & PRESENCE SERVICE DEMO ]")
    print("=" * 60)
    
    # 1. Initialize the service with tracing enabled
    print("\n[1] Initializing MembershipService (with Tracing enabled)...")
    service = MembershipService(room_id="demo-room", storage_dir="./demo_storage", enable_tracing=True)
    
    # Start the tick scheduler (background thread)
    service.start_tick_scheduler(interval_s=1.0)
    time.sleep(0.5)

    # 2. Distribution Team: Subscribe to real-time events
    print("\n[2] Distribution Team: Subscribing to live membership events...")
    def on_membership_event(event, delta):
        print(f"   [EVENT] User '{delta.user_id}' is now {delta.type.upper()}! (Version {event.membership_version})")
    
    service.subscribe_membership_events(on_membership_event)

    # 3. History Team: Register the auto-backfill handler
    print("\n[3] History Team: Registering auto-backfill handler...")
    def history_handler(user_id, event):
        print(f"   [HISTORY] Auto-triggered! Starting backfill for '{user_id}'...")
        service.start_history_backfill(user_id)
        
        # Simulate time passing while history team replays messages
        time.sleep(1.5)
        
        print(f"   [HISTORY] Replay complete! Marking '{user_id}' as ACTIVE.")
        service.complete_history_backfill(user_id)
        
    service.register_history_handler(history_handler)

    # 4. Security Team: Register a join validator
    print("\n[4] Security Team: Registering Ban-List Validator...")
    def security_validator(user_id, display_name, context):
        if user_id == "eve":
            print(f"   [SECURITY] Rejected join request for banned user: {user_id}")
            class Reject:
                accepted = False
                reason = "User is on the ban list"
            return Reject()
        class Accept:
            accepted = True
        return Accept()
        
    service.register_join_validator(security_validator)

    # ---------------------------------------------------------
    # DEMO ACTIONS
    # ---------------------------------------------------------

    print("\n" + "=" * 60)
    print("ACTION: Banned User Tries to Join")
    print("=" * 60)
    result = service.join_member("eve", "Eve")
    print(f"Result -> Accepted: {result.accepted}")

    print("\n" + "=" * 60)
    print("ACTION: Multiple Legitimate Users Join")
    print("=" * 60)
    
    users_to_join = [("alice", "Alice"), ("bob", "Bob"), ("charlie", "Charlie"), ("dave", "Dave")]
    
    for uid, name in users_to_join:
        print(f"\n--- {name} is joining ---")
        service.join_member(uid, name)
        # Give history team a moment to process the backfill
        time.sleep(1.6) 
    
    print("\n" + "=" * 60)
    print("ACTION: Some Users Leave")
    print("=" * 60)
    
    print("\n--- Bob is leaving ---")
    service.leave_member("bob")
    time.sleep(0.5)
    
    print("\n--- Charlie is leaving ---")
    service.leave_member("charlie")
    time.sleep(0.5)

    # Let's check the snapshot
    print("\n[SNAPSHOT] Current Room Snapshot:")
    snap = service.get_membership_snapshot()
    for uid, member in snap.members.items():
        print(f"   - {member.display_name} (ID: {uid}) -> State: {member.state.name}")

    # ---------------------------------------------------------
    # TRACING & CLEANUP
    # ---------------------------------------------------------
    
    print("\n" + "=" * 60)
    print("Dapper-Style Tracing Output")
    print("=" * 60)
    print("Showing the exact microsecond lifecycle of Alice's join flow:\n")
    
    traces = service._coordinator.tracer.get_recent_traces(limit=1)
    if traces:
        trace = traces[0]
        print(f"Trace ID: {trace.trace_id}")
        print(f"User ID:  {trace.user_id}")
        print(f"Total Duration: {trace.total_duration_ms:.2f} ms")
        print("Spans:")
        for span in trace.spans:
            print(f"   -> [{span.timestamp:.4f}] {span.name}")
    
    # Clean up
    print("\n[5] Stopping background scheduler and cleaning up...")
    service.stop_tick_scheduler()
    print("[5] Demo Complete!")


if __name__ == "__main__":
    run_demo()
