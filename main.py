"""
Run the chat UI with an optional distribution node and hybrid encryption enabled.

Set MOCK_DATA_ENABLED=false to use live distribution + encryption (default for this entry).
"""

from __future__ import annotations

import os
import threading

import requests

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import PubkeyRoster, SecureChatSession
from ui.app import create_app


def run_ui(app, debug=True, host="127.0.0.1", port=5050):
    app.run(debug=debug, host=host, port=port, use_reloader=False)


def get_external_ip() -> str:
    try:
        response = requests.get("https://api.ipify.org?format=text", timeout=5)
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException:
        return "127.0.0.1"


def run_distribution_node(node: BroadcastNode):
    node.start()


def main():
    os.environ.setdefault("MOCK_DATA_ENABLED", "false")

    host = os.getenv("PEERCHAT_HOST", get_external_ip())
    port = int(os.getenv("PEERCHAT_PORT", "5000"))
    node_address = f"{host}:{port}"

    registry = InMemoryRegistry()
    registry.add_peer(host, port)

    roster = PubkeyRoster()
    session = SecureChatSession(user_id=node_address, roster=roster)

    node = BroadcastNode(host, port, registry)
    app = create_app()

    app.chat_service.node_address = node_address
    app.chat_service._secure = session
    app.chat_service._broadcast_message = node.broadcast

    def on_message(msg: Message) -> None:
        app.chat_service.message_received(msg)

    node.on_message = on_message

    dist_thread = threading.Thread(target=run_distribution_node, args=(node,), daemon=True)
    dist_thread.start()

    ui_thread = threading.Thread(
        target=run_ui,
        args=(app,),
        kwargs={"debug": False, "host": "127.0.0.1", "port": 5050},
        daemon=True,
    )
    ui_thread.start()
    ui_thread.join()


if __name__ == "__main__":
    main()
