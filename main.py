from ui.app import create_app
from distribution.broadcast_node import BroadcastNode
from distribution.message import Message
from distribution.peer_registry import InMemoryRegistry
import threading
import requests



def run_ui(app, debug=True, host="127.0.0.1", port=5050):
    app.run(debug=debug, host=host, port=port)

def get_external_ip() -> str:
    try:
        response = requests.get("https://api.ipify.org?format=text", timeout=5)
        response.raise_for_status()
        return response.text.strip()
    except requests.RequestException:
        return "Unknown"
    
def run_distribution_node(node: BroadcastNode):
    node.start()

def main():
    app = create_app()
    
    node = BroadcastNode(host=get_external_ip(), port=5000)
    node.on_message = lambda msg: app.chat_service.message_received(msg)
    app.chat_service.message_out = lambda content: node.broadcast(Message(content=content, sender=node.address))
    peer_registry = InMemoryRegistry()
    app.chat_service.peer_registry = peer_registry
    
    distribution_thread = threading.Thread(target=run_distribution_node, args=(node,))
    distribution_thread.daemon = True
    distribution_thread.start()
    
    ui_thread = threading.Thread(target=run_ui, args=(app,), kwargs={"debug": True, "host": "127.0.0.1", "port": 5050})
    ui_thread.daemon = True
    ui_thread.start()
    ui_thread.join()

if __name__ == "__main__":
    main()