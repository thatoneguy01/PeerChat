"""CLI for manual/local E2E mesh runs (like docker-compose up/down)."""

from __future__ import annotations

import argparse
import sys

from e2e.mesh import PeerMesh


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PeerChat local E2E mesh")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="start mesh, send a message, assert delivery")
    run_p.add_argument("--peers", type=int, default=3)
    run_p.add_argument("--text", default="E2E hello from mesh CLI")
    run_p.add_argument("--base-ws-port", type=int, default=5201)
    run_p.add_argument("--timeout", type=float, default=12.0)
    run_p.add_argument("--log-dir", default=None)

    sub.add_parser("help-ports", help="print default port layout")

    args = parser.parse_args(argv)

    if args.command == "help-ports":
        print("WS ports:     5201, 5202, 5203, ...")
        print("Control HTTP: 15201, 15202, 15203, ...")
        return 0

    if args.command == "run":
        mesh = PeerMesh(peer_count=args.peers, base_ws_port=args.base_ws_port)
        print(f"Starting {args.peers} peers...")
        try:
            mesh.start(log_dir=args.log_dir)
            mesh.clear_all_inboxes()
            print(f"Sending from peer 0: {args.text!r}")
            sent = mesh.send_from(0, args.text)
            print(f"  message id: {sent.get('id')}")

            results = mesh.wait_for_plaintext(
                args.text, timeout=args.timeout
            )
            mesh.assert_encrypted_on_wire(args.text)

            print(f"Delivered to {len(results)}/{args.peers} peers:")
            for addr, msgs in results.items():
                print(f"  {addr}: {len(msgs)} message(s)")

            if len(results) < args.peers:
                return 1
            print("E2E mesh run PASSED")
            return 0
        finally:
            mesh.stop()

    return 1


if __name__ == "__main__":
    sys.exit(main())
