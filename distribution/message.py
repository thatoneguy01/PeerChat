import uuid
import time
import json
from dataclasses import dataclass, field, asdict


@dataclass
class Message:
    """
    A single chat message passed through the gossip network.

    Fields owned by other teams:
      - signature: security team fills this before calling broadcast()
      - ttl: controls how many hops the gossip travels (default 10)
    """
    content: str
    sender: str                                     # "host:port" of the originating node
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    signature: str = ""                             # set by security team
    ttl: int = 10                                   # decremented at each gossip hop
    vector_clock: dict = field(default_factory=dict)  # set by GossipNode on send

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(raw: str) -> "Message":
        return Message(**json.loads(raw))
