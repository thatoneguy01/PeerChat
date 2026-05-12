# Message History, Recovery, and Snapshot Plan

## Goal

This module is responsible for saving chat messages locally and helping new or
returning nodes catch up with message history.

The message distribution module handles live delivery of one chat `Message`.
This module handles:

- Saving messages on each node
- Keeping a local message log
- Periodically creating snapshots of old log
- Sending old messages to new nodes
- Sending old messages to nodes that were offline
- Using chunks to transfer messages
- Using streaming to send chunks step by step
- Avoiding duplicate messages
- Helping a node catch up with recent messages
- Creating snapshots and compacting old logs

## Relationship With Message Distribution

Message distribution already defines the live chat message format:

```python
Message(
    content: str,
    sender: str,
    id: str,
    timestamp: float,
    signature: str,
    ttl: int,
    vector_clock: dict,
)
```

This module should reuse the existing `Message` format for stored chat messages.
It should also use `message.id` for duplicate detection and `message.vector_clock`
to decide which messages a returning node is missing.

Normal live chat messages should still use:

```python
BroadcastNode.broadcast(message)
```

History recovery should not use `broadcast()`, because recovery is a direct
one-to-one transfer between two peers. Snapshot chunks and history chunks should
not appear as normal chat messages in the UI.

## Local Message Log

Each node should keep an append-only local log for recent messages.

Recommended file:

```text
logs/active.log.jsonl
```

Each line should store one full serialized `Message`.

Example:

```json
{
  "id": "7355eaeb-...",
  "content": "hello",
  "sender": "127.0.0.1:5001",
  "timestamp": 1747058342.71,
  "signature": "",
  "ttl": 10,
  "vector_clock": {
    "127.0.0.1:5001": 3
  }
}
```

The log should preserve enough information to replay the message later exactly
as it was originally received.

## Local Indexes

The log is good for append performance, but indexes are needed for fast lookup.

Recommended indexes:

```text
index/message_id.index
index/sender_seq.index
index/latest_vector_clock.json
```

`message_id.index` helps avoid duplicates.

`sender_seq.index` maps each sender and vector-clock sequence number to a stored
message location.

`latest_vector_clock.json` records the newest message sequence this node has
persisted from each sender.

Example:

```json
{
  "127.0.0.1:5001": 120,
  "127.0.0.1:5002": 98,
  "127.0.0.1:5003": 144
}
```

This latest vector clock becomes the node's recovery cursor.

## Snapshot

A snapshot is a compacted storage checkpoint for older history.

Because chat history needs to preserve old messages, the snapshot should still
contain the messages it covers. The main difference is that the snapshot is
compressed and no longer part of the active append-only log.

Recommended files:

```text
snapshots/snapshot-0001.meta.json
snapshots/snapshot-0001.jsonl.gz
```

The compressed `.jsonl.gz` file stores older serialized `Message` records.

The metadata file stores what the snapshot covers:

```json
{
  "snapshot_id": "snapshot-0001",
  "created_at": 1747058342.71,
  "covers_until_vector_clock": {
    "127.0.0.1:5001": 120,
    "127.0.0.1:5002": 98
  },
  "message_count": 1000,
  "checksum": "sha256..."
}
```

After a snapshot is safely written and verified, old active log entries covered
by that snapshot can be deleted or compacted.

## Saving a New Message

When this node receives a live message from message distribution:

```text
1. Receive Message from on_message callback.
2. Check message.id in local index.
3. If the message already exists, ignore it.
4. If the message is new, append it to active.log.jsonl.
5. Update message_id.index.
6. Update sender_seq.index.
7. Update latest_vector_clock.json.
```

The sender sequence can be read from:

```python
sender_seq = msg.vector_clock.get(msg.sender, 0)
```

## Recovery Request

When a node joins or returns after being offline, it should ask one peer for
missing history.

Example request:

```json
{
  "type": "recover_request",
  "node": "127.0.0.1:5004",
  "have_vector_clock": {
    "127.0.0.1:5001": 120,
    "127.0.0.1:5002": 98
  },
  "known_snapshot_id": "snapshot-0001"
}
```

A brand-new node can send:

```json
{
  "type": "recover_request",
  "node": "127.0.0.1:5004",
  "have_vector_clock": {},
  "known_snapshot_id": null
}
```

## Choosing Messages to Send

The recovery provider compares the returning node's cursor with local history.

For each stored message:

```python
sender_seq = msg.vector_clock.get(msg.sender, 0)
peer_seq = have_vector_clock.get(msg.sender, 0)
should_send = sender_seq > peer_seq
```

If `should_send` is true, the peer is missing that message.

If the peer needs messages that are already compacted into a snapshot, send the
snapshot chunks first. Then send newer active-log messages as delta replay.

## Chunked Streaming

Recovery should send messages in chunks instead of one large response.

Example history chunk:

```json
{
  "type": "history_chunk",
  "transfer_id": "recover-abc",
  "chunk_id": 5,
  "is_snapshot": false,
  "messages": [
    {
      "id": "7355eaeb-...",
      "content": "hello",
      "sender": "127.0.0.1:5001",
      "timestamp": 1747058342.71,
      "signature": "",
      "ttl": 10,
      "vector_clock": {
        "127.0.0.1:5001": 3
      }
    }
  ]
}
```

Example snapshot chunk:

```json
{
  "type": "snapshot_chunk",
  "transfer_id": "recover-abc",
  "snapshot_id": "snapshot-0001",
  "chunk_id": 1,
  "total_chunks": 20,
  "messages": []
}
```

The receiver should persist each chunk and then ACK it.

Example ACK:

```json
{
  "type": "history_ack",
  "transfer_id": "recover-abc",
  "chunk_id": 5
}
```

If the sender does not receive an ACK, it can retry the same chunk.

## Recovery Flow

```text
1. Node joins or comes back online.
2. Node loads latest_vector_clock.json from local storage.
3. Node sends recover_request to a reachable peer.
4. Peer checks whether snapshot data is needed.
5. Peer streams snapshot chunks if needed.
6. Peer streams active-log delta chunks.
7. Receiver deduplicates messages by message.id.
8. Receiver appends new messages to local log.
9. Receiver updates indexes and latest vector clock.
10. Receiver ACKs each chunk.
11. Node is caught up and can continue receiving live messages normally.
```

## Open Decisions

- Exact chunk size by message count or byte size
- Whether recovery should ask one peer or multiple peers
- How many times to retry failed chunks
- How often to create snapshots
- How long to keep old snapshots
- Whether snapshot transfer should send compressed bytes or decoded messages
- Whether recovery should run on the same WebSocket port or a separate recovery port
