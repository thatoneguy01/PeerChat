from ui.app import create_app
from distribution import DistributionNode
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
    
def run_distribution_node(node: DistributionNode):
    node.start()

def main():
    node = DistributionNode(host=get_external_ip(), port=5000)
    node.on_message = lambda msg: app.chat_service.message_received(msg)
    app.chat_service.message_out = lambda content: node.broadcast(content, sender=node.address)
    distreibution_thread = threading.Thread(target=run_distribution_node, args=(node,))
    distreibution_thread.daemon = True
    distreibution_thread.start()
    app = create_app()
    ui_thread = threading.Thread(target=run_ui, args=(app,), kwargs={"debug": True, "host": "127.0.0.1", "port": 5050})
    ui_thread.daemon = True
    ui_thread.start()