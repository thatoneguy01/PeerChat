import subprocess
import time
import sys
import os

def run_network_demo():
    print("=" * 60)
    print("[ P2P NETWORK SIMULATION DEMO ]")
    print("=" * 60)
    print("Simulating a LAN environment where multiple machines")
    print("connect to each other to form a distributed chat room.\n")

    # We will spawn 3 separate processes (nodes) communicating over localhost TCP ports.
    # This exactly mimics running them on different machines over a LAN.
    
    processes = []
    
    try:
        # Node 1: The First Node (Bootstrap Node)
        print("[Node 1] Starting on port 8001 (Bootstrap Node)...")
        cmd1 = [
            sys.executable, "-m", "peer_discovery.network.cli",
            "--port", "8001",
            "--advertise", "127.0.0.1:8001",
            "--room", "lan-demo-room",
            "--name", "Alice-Laptop",
            "--storage", "./storage_node1",
            "--no-crypto"
        ]
        p1 = subprocess.Popen(cmd1, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 1 (Alice)", p1))
        
        # Wait a moment for Node 1 to bind its port
        time.sleep(2.0)
        
        # Node 2: Connects to Node 1
        print("[Node 2] Starting on port 8002... Bootstrapping via Node 1...")
        cmd2 = [
            sys.executable, "-m", "peer_discovery.network.cli",
            "--port", "8002",
            "--advertise", "127.0.0.1:8002",
            "--room", "lan-demo-room",
            "--name", "Bob-Desktop",
            "--storage", "./storage_node2",
            "--bootstrap", "127.0.0.1:8001",
            "--no-crypto"
        ]
        p2 = subprocess.Popen(cmd2, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 2 (Bob)", p2))
        
        time.sleep(2.0)
        
        # Node 3: Connects to Node 2 (will gossip through to Node 1)
        print("[Node 3] Starting on port 8003... Bootstrapping via Node 2...")
        cmd3 = [
            sys.executable, "-m", "peer_discovery.network.cli",
            "--port", "8003",
            "--advertise", "127.0.0.1:8003",
            "--room", "lan-demo-room",
            "--name", "Charlie-Phone",
            "--storage", "./storage_node3",
            "--bootstrap", "127.0.0.1:8002",
            "--no-crypto"
        ]
        p3 = subprocess.Popen(cmd3, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        processes.append(("Node 3 (Charlie)", p3))
        
        print("\n[WAITING] Letting the network run for 10 seconds. Nodes will:")
        print("   1. Exchange Bootstrap states (Snapshot transfer)")
        print("   2. Send Heartbeats to each other")
        print("   3. Gossip their membership status")
        print("-" * 60)
        
        time.sleep(10.0)
        
        print("\n" + "=" * 60)
        print("[STOPPING] STOPPING NETWORK AND GATHERING LOGS")
        print("=" * 60)
        
    finally:
        # Terminate all processes
        for name, p in processes:
            p.terminate()
            
        # Give them a second to cleanly shut down
        time.sleep(1)
        
        # Print a snippet of their logs to show they communicated
        for name, p in processes:
            print(f"\n--- Output Snippet from {name} ---")
            out, _ = p.communicate()
            lines = out.splitlines()
            # Just print the interesting network lines, filter out raw startup noise
            for line in lines[-15:]:
                if "network" in line.lower() or "gossip" in line.lower() or "heartbeat" in line.lower() or "join" in line.lower():
                    print(line)

    print("\n[SUCCESS] Simulation Complete!")
    print("If you want to run this manually across multiple terminal windows, see the CLI commands inside this script!")


if __name__ == "__main__":
    run_network_demo()
