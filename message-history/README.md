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
- recover the whole system?

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

History recovery should use `BroadcastNode.send_to_peer(host, port, message)`
so each chunk goes only to the recovering peer. Snapshot chunks and history
chunks should not appear as normal chat messages in the UI.

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

```
logs/
  active.log.jsonl
```

> **Implemented in** `storage/local_message_store.py` — `save(msg)` appends each
> message as a compact JSON line. Duplicate messages are dropped before writing
> using `message_id.index`.

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

```
index/
  message_id.index
  sender_seq.index
  latest_vector_clock.json
```

> **Implemented in** `storage/local_message_store.py` — all three indexes are
> loaded into memory on startup and flushed to disk after every `save()`. Writes
> use `.tmp` + `os.replace()` to prevent corruption on crash.

## Snapshot

A snapshot is a compacted storage checkpoint for older history.

Because chat history needs to preserve old messages, the snapshot should still
contain the active-log messages it covers. The main difference is that the
snapshot is compressed and no longer part of the active append-only log.

By default, each node automatically creates a snapshot after 200 messages have
accumulated in `active.log.jsonl`. The snapshot contains only the current active
log contents, not older snapshot contents, then the active log is cleared.

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

> **Implemented in** `storage/local_message_store.py` — `save()` triggers
> automatic snapshotting when the active log reaches 200 messages.
> `create_snapshot()` writes both files atomically enough for local recovery,
> `read_snapshot_messages()` validates the checksum before reading, and
> `get_missing_since()` reads from snapshots plus the active log so compacted
> messages can still be replayed.

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

When a node joins or returns after being offline, it sends a `recover_request`
to all active peers. The request contains the target node's latest vector clock.
Each active peer compares that vector clock with its own local history and sends
back only the messages the target is missing.

Example request:

```json
{
  "type": "recover_request",
  "transfer_id": "recover-abc",
  "requester_id": "127.0.0.1:5004",
  "requester_host": "127.0.0.1",
  "requester_port": 5004,
  "have_vector_clock": {
    "127.0.0.1:5001": 120,
    "127.0.0.1:5002": 98
  }
}
```

A brand-new node can send:

```json
{
  "type": "recover_request",
  "transfer_id": "recover-abc",
  "requester_id": "127.0.0.1:5004",
  "requester_host": "127.0.0.1",
  "requester_port": 5004,
  "have_vector_clock": {}
}
```

## All-Peer Vector Clock Check

The target does not know which peer has the missing messages. It only knows what
it already has. That is why the target sends one recovery request to every
active peer.

```text
Target -> Peer B: recover_request + target vector clock
Target -> Peer C: recover_request + target vector clock
Target -> Peer D: recover_request + target vector clock
```

The vector clock does not know what B, C, or D have. Each peer checks its own
local store.

Example:

```text
Target vector clock:
  {"A": 2}

Peer B local history:
  A:1, A:2, A:3
  -> B sends A:3

Peer C local history:
  A:1, A:2
  -> C sends nothing

Peer D local history:
  A:1, A:2, A:3, A:4
  -> D sends A:3, A:4
```

If multiple peers send the same message, the target stores it once and skips the
duplicate by `message.id`.

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
  "source_user_id": "127.0.0.1:5001",
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
3. Node sends recover_request to all active peers.
4. Each active peer checks the target vector clock against its local store.
5. Peers with missing messages build history chunks.
6. Peers send chunks directly with send_to_peer().
7. Target receives chunks from one or more peers.
8. Target deduplicates messages by message.id.
9. Target appends new messages to local log.
10. Target updates indexes and latest vector clock.
11. Node is caught up as chunks arrive.
```

## Open Decisions

- Exact chunk size by message count or byte size
- Whether all-peer fanout should later be capped for very large rooms
- How many times to retry failed chunks
- How often to create snapshots
- How long to keep old snapshots
- Whether snapshot transfer should send compressed bytes or decoded messages
- Whether recovery should run on the same WebSocket port or a separate recovery port

## Tasks 1 & 2 — Local Message Storage and Log

### What This Does

Every node saves incoming chat messages locally so they can be replayed later
to new or returning members. This covers two responsibilities:

- **Task 1:** Saving each message to disk as it arrives
- **Task 2:** Keeping a local log and indexes for fast lookup and recovery

---

### File Structure

```
storage/
  __init__.py               # exports Message and LocalMessageStore
  models.py                 # Message dataclass (reuses distribution team format)
  local_message_store.py    # all storage logic

logs/
  active.log.jsonl          # append-only message log (one JSON line per message)

index/
  message_id.index          # JSON list of stored message IDs (duplicate detection)
  sender_seq.index          # JSON map of sender → {seq → byte offset in log}
  latest_vector_clock.json  # JSON map of sender → latest seq (recovery cursor)
```

> `logs/` and `index/` are created at runtime and ignored by git.

---

### How It Works

When a message arrives via the `on_message` callback from message distribution:

```
1. Check message.id against message_id.index — duplicate? drop it.
2. Append full Message as one JSON line to active.log.jsonl.
3. Update message_id.index (add message.id).
4. Update sender_seq.index (sender + seq → byte offset).
5. Update latest_vector_clock.json (only if new seq > current stored seq).
```

All five steps run under a threading lock so concurrent messages stay consistent.
Index writes use a `.tmp` file + `os.replace()` so a crash mid-write never
corrupts an index file.

---

### Key Classes

#### `Message` (`models.py`)

Reuses the distribution team's message format exactly. Adds two helpers:

- `to_json()` — compact JSON string for writing to the log
- `from_json(line)` — deserializes a log line back to a Message, with safe
  `.get()` defaults so older log lines don't break recovery
- `sender_seq()` — returns `vector_clock.get(sender, 0)`

#### `LocalMessageStore` (`local_message_store.py`)

Main class. Instantiate once per node:

```python
from storage import LocalMessageStore

# By default, snapshots are created after 200 active-log messages.
store = LocalMessageStore()

# Tests or demos can override the threshold.
small_store = LocalMessageStore(snapshot_threshold=10)

# Save an incoming message (returns True if saved, False if duplicate)
store.save(msg)

# Get recent messages for backfill
messages = store.get_recent(limit=100)

# Get this node's vector clock for recovery requests
vc = store.get_latest_vector_clock()

# Save a recovered history chunk.
# Duplicate IDs are skipped so chunk retries are safe.
result = store.save_many(messages)
# {"saved": 3, "duplicates": 1, "invalid": 0}

# Find messages a returning peer is missing.
missing = store.get_missing_since({
    "127.0.0.1:5001": 120,
    "127.0.0.1:5002": 98,
})

# Build serializable direct-send history chunks for that peer.
chunks = store.build_history_chunks(
    have_vector_clock={"127.0.0.1:5001": 120},
    transfer_id="recover-abc",
    chunk_size=100,
)

# Manually create a compressed snapshot if needed. compact=True clears
# active.log.jsonl after the snapshot is safely written; recovery still reads
# snapshot + active log.
meta = store.create_snapshot(snapshot_id="snapshot-0001", compact=True)

# Inspect or read snapshots.
snapshots = store.list_snapshots()
messages = store.read_snapshot_messages("snapshot-0001")
```

#### Duplicate Handling During Recovery

Recovery receivers should call `save_many(messages)` for each received
`history_chunk`. Internally, this reuses `save(msg)`, so duplicate detection is
still based on `message.id` and all normal indexes stay consistent.

This makes recovery chunk retries idempotent. If an ACK is lost and the same
chunk arrives again, already stored messages are counted as `duplicates` and are
not appended to `active.log.jsonl` a second time.

#### Catching Up With Recent Messages

Recovery providers should call `get_missing_since(have_vector_clock)` or
`build_history_chunks(...)` when answering a `recover_request`.

For each locally stored message, the store compares:

```python
sender_seq = msg.vector_clock.get(msg.sender, 0)
peer_seq = have_vector_clock.get(msg.sender, 0)
```

The message is returned when `sender_seq > peer_seq`. Multiple senders are
handled independently, and returned messages preserve active-log order.

`build_history_chunks(...)` returns payloads shaped like:

```json
{
  "type": "history_chunk",
  "transfer_id": "recover-abc",
  "chunk_id": 1,
  "is_snapshot": false,
  "is_last": true,
  "messages": []
}
```

`HistoryChunkStreamer` wraps these chunks in distribution `Message` objects and
streams them with `BroadcastNode.send_to_peer()`. Distribution sends each chunk
only to the requested host and port, with no room-wide fanout.

```python
from storage import HistoryChunkStreamer

streamer = HistoryChunkStreamer(
    store=store,
    broadcaster=node,
    self_user_id=node.address,
)

streamer.stream_missing_history(
    target_host="127.0.0.1",
    target_port=5004,
    have_vector_clock={"127.0.0.1:5001": 120},
    transfer_id="recover-abc",
    chunk_size=100,
)
```

New or recovering nodes can also send a request through the same adapter:

```python
streamer.send_recover_request(
    provider_host="127.0.0.1",
    provider_port=5001,
    requester_host="127.0.0.1",
    requester_port=5004,
    transfer_id="recover-abc",
)
```

When a provider receives that `recover_request` through
`handle_transport_message()`, it reads the requester's `have_vector_clock`,
selects missing messages from snapshots plus the active log, and streams
`history_chunk` messages back with `send_to_peer()`.

#### Snapshots and Compaction

`create_snapshot()` writes:

```text
snapshots/<snapshot_id>.jsonl.gz
snapshots/<snapshot_id>.meta.json
```

The compressed JSONL file contains full serialized `Message` records. The meta
file stores the snapshot id, creation time, message count, checksum, data file,
and `covers_until_vector_clock`.

When `compact=True`, the active log is truncated after the snapshot is written.
The store rebuilds indexes afterward, and reads continue to combine snapshots
with the active log. This means `get_recent()`, `get_missing_since()`, and
`build_history_chunks()` still see compacted messages.

Automatic snapshots use `compact=True`. With the default threshold:

```text
messages 1-200   -> snapshots/snapshot-0001.jsonl.gz, active log cleared
messages 201-400 -> snapshots/snapshot-0002.jsonl.gz, active log cleared
messages 401-... -> active.log.jsonl until the next threshold
```

---

### Running Tests

```bash
# All tests
make test

# Storage tests only
make test-local

# Edge case tests only
make test-edge

# Clean logs, indexes, and cache
make clean
```

---

### What's Handed Off to Other Tasks

| What                        | Where it goes                                      |
| --------------------------- | -------------------------------------------------- |
| `get_recent(limit)`         | Membership backfill (sends history to new members) |
| `get_latest_vector_clock()` | Recovery tasks 3+ (sent in `recover_request`)      |
| `save(msg)`                 | Called by recovery receiver when replaying chunks  |
| `save_many(messages)`       | Recovery receiver chunk ingestion and retry dedupe |
| `get_missing_since(vc)`     | Recovery provider delta selection                  |
| `build_history_chunks(...)` | Recovery provider chunked direct-send payloads     |
| `create_snapshot(...)`      | Compacts older local history into compressed files |
| `read_snapshot_messages()`  | Reads verified snapshot contents for recovery      |
