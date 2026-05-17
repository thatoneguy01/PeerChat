from ui.app import create_app
from distribution.broadcast_node import BroadcastNode
from distribution.message import Message
from distribution.peer_registry import InMemoryRegistry
import threading
import socket, time
from utils import get_external_ip



def run_ui(app, debug=True, host="127.0.0.1", port=5050):
    app.run(debug=debug, host=host, port=port)
    

def main():
    app = create_app()
    
    peer_registry = InMemoryRegistry()
    # node = BroadcastNode(host=socket.gethostbyname(socket.gethostname()), port=5020, peer_registry=peer_registry)
    node = BroadcastNode(host="0.0.0.0", port=5000, peer_registry=peer_registry)
    node.on_message = lambda msg: app.chat_service.message_received(msg)
    app.chat_service.message_out = lambda content: node.broadcast(Message(content=content, sender=node.address))
    app.chat_service.peer_registry = peer_registry
    node.start()
    time.sleep(3)
    
    app.run(debug=True, host="127.0.0.1", port=5050)
    # ui_thread = threading.Thread(target=run_ui, args=(app,), kwargs={"debug": True, "host": "127.0.0.1", "port": 5050})
    # ui_thread.daemon = True
    # ui_thread.start()
    # ui_thread.join()

if __name__ == "__main__":
    main()