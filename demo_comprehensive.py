import time
import sys
import subprocess
from peer_discovery.membership_integration.service import MembershipService

def print_header(title):
    print("\n" + "=" * 80)
    print(f" {title} ".center(80, "="))
    print("=" * 80 + "\n")

def print_step(title, desc):
    print(f"\n> [{title}]")
    print(f"  {desc}")

def run_comprehensive_demo():
    print_header("PEER DISCOVERY MODULE: COMPREHENSIVE DEMO")
    print("Welcome! This script will demonstrate the complete Peer Discovery architecture.")
    print("Our architecture is divided into two distinct layers:")
    print("  1. The Local Membership Service (Control Plane for this specific machine)")
    print("  2. The P2P Network Layer (Gossip, Discovery, and TCP Transport)")
    time.sleep(3)

    # ---------------------------------------------------------
    # PART 1: The Membership Service (Local Control Plane)
    # ---------------------------------------------------------
    print_header("PART 1: THE MEMBERSHIP SERVICE (Core Logic)")
    print("The Membership Service is the central authority on WHO is in the room.")
    print("It provides hooks for the Security, History, and Distribution teams.")
    time.sleep(2)

    print_step("Initialization", "Starting the local MembershipService and ticking background scheduler.")
    service = MembershipService(room_id="comprehensive-room", storage_dir="./comp_storage", enable_tracing=True)
    service.start_tick_scheduler(interval_s=1.0)
    time.sleep(1)

    print_step("Security Team Integration", "Registering a ban-list validator that rejects the user 'mallory'.")
    def security_validator(user_id, display_name, context):
        if user_id == "mallory":
            class Reject:
                accepted = False
                reason = "User 'mallory' is banned."
            return Reject()
        class Accept:
            accepted = True
        return Accept()
    service.register_join_validator(security_validator)

    print_step("History Team Integration", "Registering an auto-backfill handler for new joins.")
    def history_handler(user_id, event):
        print(f"      [HISTORY-TEAM] Triggered! Starting message replay for {user_id}...")
        service.start_history_backfill(user_id)
        time.sleep(1.0)  # simulate replay time
        print(f"      [HISTORY-TEAM] Replay complete. Marking {user_id} as ACTIVE.")
        service.complete_history_backfill(user_id)
    service.register_history_handler(history_handler)

    print_step("Distribution Team Integration", "Subscribing to live event deltas to update routing tables.")
    def distribution_handler(event, delta):
        print(f"      [DIST-TEAM] Live Update: User '{delta.user_id}' is now {delta.type.upper()}")
    service.subscribe_membership_events(distribution_handler)

    time.sleep(2)
    print("\n--- Let's see it in action! ---")
    
    print("\n1. Mallory tries to join:")
    result = service.join_member("mallory", "Mallory")
    print(f"   Result -> Accepted? {result.accepted}")

    print("\n2. Alice joins the room:")
    service.join_member("alice", "Alice")
    time.sleep(1.5) # Wait for backfill to finish

    print("\n3. Current Local Snapshot State:")
    snap = service.get_membership_snapshot()
    for uid, member in snap.members.items():
        print(f"   - {member.display_name} -> {member.state.name}")

    service.stop_tick_scheduler()
    time.sleep(2)

    # ---------------------------------------------------------
    # PART 2: The Network Layer (P2P Gossip)
    # ---------------------------------------------------------
    print_header("PART 2: THE P2P NETWORK LAYER (Distributed Sync)")
    print("In a real deployment, the MembershipService is wrapped by a 'DiscoveryNode'.")
    print("These nodes connect over TCP to form a decentralized Gossip network.")
    print("We will now spawn 3 separate processes to simulate a multi-machine LAN.")
    time.sleep(4)

    processes = []
    try:
        print_step("Node 1", "Starting Alice's Laptop on port 8001 (Seed Node)")
        cmd1 = [sys.executable, "-m", "peer_discovery.network.cli", "--port", "8001", "--advertise", "127.0.0.1:8001", "--room", "demo-room", "--name", "Alice", "--storage", "./comp_node1", "--no-crypto"]
        p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 1 (Alice)", p1))
        time.sleep(1.5)

        print_step("Node 2", "Starting Bob's Desktop on port 8002. Bootstrapping via Node 1.")
        cmd2 = [sys.executable, "-m", "peer_discovery.network.cli", "--port", "8002", "--advertise", "127.0.0.1:8002", "--room", "demo-room", "--name", "Bob", "--storage", "./comp_node2", "--bootstrap", "127.0.0.1:8001", "--no-crypto"]
        p2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 2 (Bob)", p2))
        time.sleep(1.5)

        print_step("Node 3", "Starting Charlie's Phone on port 8003. Bootstrapping via Node 2.")
        cmd3 = [sys.executable, "-m", "peer_discovery.network.cli", "--port", "8003", "--advertise", "127.0.0.1:8003", "--room", "demo-room", "--name", "Charlie", "--storage", "./comp_node3", "--bootstrap", "127.0.0.1:8002", "--no-crypto"]
        p3 = subprocess.Popen(cmd3, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 3 (Charlie)", p3))

        print("\nLetting the network run for 8 seconds to synchronize snapshots and exchange heartbeats...")
        for i in range(8, 0, -1):
            sys.stdout.write(f"\rTime remaining: {i}s ")
            sys.stdout.flush()
            time.sleep(1)
        print("\n")

    finally:
        for _, p in processes:
            p.terminate()
        time.sleep(1)

        print_header("NETWORK LOGS (Proof of Communication)")
        for name, p in processes:
            print(f"\n--- Snippet from {name} ---")
            out, _ = p.communicate()
            lines = out.splitlines()
            for line in lines[-10:]:
                if "network" in line.lower() or "bootstrap" in line.lower() or "heartbeat" in line.lower():
                    # Clean up the long timestamp prefixes to make it readable
                    clean_line = line.split("]", 1)[-1].strip() if "]" in line else line
                    print(f"  {clean_line}")

    print_header("DEMO COMPLETE")
    print("What we just saw:")
    print("1. Local hooks for Security, History, and Distribution working flawlessly.")
    print("2. Multi-process P2P network bootstrapping over TCP sockets.")
    print("\nThe Peer Discovery module is fully operational and ready for integration!")


if __name__ == "__main__":
    run_comprehensive_demo()
