from dataclasses import dataclass, field
from typing import Dict
import json


@dataclass
class Message:
    """
    Reuses the Message format defined by the Message Distribution team.
    vector_clock is per-message: {sender_address: sequence_number}
    """
    id: str
    content: str
    sender: str
    timestamp: float
    signature: str
    ttl: int
    vector_clock: Dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        """Serialize to a single JSON line for writing to active.log.jsonl."""
        return json.dumps(
            {
                "id": self.id,
                "content": self.content,
                "sender": self.sender,
                "timestamp": self.timestamp,
                "signature": self.signature,
                "ttl": self.ttl,
                "vector_clock": self.vector_clock,
            },
            separators=(",", ":"),
        )

    @staticmethod
    def from_json(line: str) -> "Message":
        """Deserialize one JSON line from the log back into a Message object."""
        data = json.loads(line)

        return Message(
            id=data["id"],
            content=data["content"],
            sender=data["sender"],
            timestamp=float(data["timestamp"]),
            signature=data.get("signature", ""),
            ttl=int(data.get("ttl", 0)),
            vector_clock={
                sender: int(seq)
                for sender, seq in data.get("vector_clock", {}).items()
            },
        )

    def sender_seq(self) -> int:
        """
        Returns this message's sequence number from its own sender's
        perspective in the vector clock.
        """
        return int(self.vector_clock.get(self.sender, 0))