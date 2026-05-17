from ui.app import create_app
from distribution.broadcast_node import BroadcastNode
from distribution.message import Message
from distribution.peer_registry import InMemoryRegistry
from security.key_storage import InMemoryKeyStore
from security.persistent_key_storage import get_platform_key_storage
from security.key_bootstrap import initialize_private_key_store
from security import configure_private_key
import threading
import socket, time
from utils import get_external_ip
from peer_discovery.network.net_utils import get_lan_ip
from message_history.storage import HistoryService


def run_ui(app, debug=True, host="127.0.0.1", port=5050):
    app.run(debug=debug, host=host, port=port)


def main():
    app = create_app()

    key_store = InMemoryKeyStore()
    persistent_storage = get_platform_key_storage()
    public_key_pem = initialize_private_key_store(key_store, persistent_storage)
    configure_private_key(key_store.get_private_key())
    app.chat_service.key_store = key_store
    app.chat_service.public_key_pem = public_key_pem
    
    peer_registry = InMemoryRegistry()
    lan_ip = get_lan_ip()
    # node = BroadcastNode(host=socket.gethostbyname(socket.gethostname()), port=5020, peer_registry=peer_registry)
    node = BroadcastNode(host=lan_ip, port=5678, peer_registry=peer_registry)
    node.own_public_key_pem = public_key_pem
    app.chat_service.peer_registry = peer_registry

    history = HistoryService(
        node=node,
        host=node.host,
        port=node.port,
    )
    history.start()
    app.chat_service.use_history(history)

    app.chat_service.node_address = node.address
    node.on_message = lambda msg: app.chat_service.message_received(msg)
    app.chat_service.message_out = lambda msg: node.broadcast(msg)
    node.start()
    time.sleep(3)
    
    app.run(debug=True, host="127.0.0.1", port=5050, use_reloader=False)
    # ui_thread = threading.Thread(target=run_ui, args=(app,), kwargs={"debug": True, "host": "127.0.0.1", "port": 5050})
    # ui_thread.daemon = True
    # ui_thread.start()
    # ui_thread.join()

if __name__ == "__main__":
    main()
