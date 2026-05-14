# Message History Integration

This file explains what the History / Recovery / Storage team provides and how
other teams should use it.

## What This Module Does

- Stores normal chat messages locally.
- Sends old messages to a new node.
- Sends missed messages to a node that was offline.
- Splits old messages into chunks.
- Uses vector clocks to send only messages the target is missing.
- Deduplicates recovered messages at the target by message ID.

## Main Classes And Functions

```python
from storage import (
    LocalMessageStore,
    HistoryChunkStreamer,
    request_missing_history_from_all_peers,
    wire_node,
)
```

### `LocalMessageStore`

Stores messages and tracks the latest vector clock.

Important methods:

```python
store.save(msg)
store.save_many(messages)
store.get_latest_vector_clock()
store.build_history_chunks(have_vector_clock, transfer_id, chunk_size=100)
```

Default chunk size is `100`.

### `HistoryChunkStreamer`

Handles recovery messages.

```python
streamer = HistoryChunkStreamer(
    store=store,
    broadcaster=node,
    self_user_id="127.0.0.1:5001",
)
```

It can:

- send missing history to one peer
- ask one peer for history
- handle incoming `recover_request` and `history_chunk` messages

### `request_missing_history_from_all_peers`

This is used when the local node wants to recover missed history.

```python
request_missing_history_from_all_peers(
    streamer=streamer,
    requester_host="127.0.0.1",
    requester_port=5002,
)
```

It sends a recovery request to all active peers from the peer registry.

## Basic Flow

### New Node Or Offline Node Pulls History

```text
target node starts or reconnects
  -> target gets its latest vector clock
  -> target sends recover_request to all active peers
  -> each peer compares target VC with local history
  -> each peer sends missing messages back as history_chunk messages
  -> target saves chunks and dedups by message ID
```

### Peer Sends Old Messages

```text
peer receives recover_request
  -> build missing messages using target vector clock
  -> split messages into chunks
  -> send each chunk with send_to_peer()
```

We do not use `broadcast()` for recovery chunks.

## What Distribution Team Needs To Know

History needs `BroadcastNode.on_message` to receive delivered messages.

Use `wire_node()` for simple setup:

```python
wiring = wire_node(
    node=node,
    host="127.0.0.1",
    port=5002,
)
```

`wire_node()`:

- saves normal chat messages
- handles recovery messages
- starts pull recovery on startup by default

Important:

- Do not call `node.deduplicate()` inside the History listener.
- Distribution already dedups before calling `on_message`.
- History uses `send_to_peer()` for recovery chunks.
- UI display logic is not handled here.

## What Peer Discovery Team Needs To Know

For new joins:

```text
JOIN_ACCEPTED
  -> Peer Discovery calls start_history_backfill(user_id)
  -> Peer Discovery asks History to stream old messages to that user
  -> Peer Discovery calls complete_history_backfill(user_id)
```

For reconnects:

```text
RECONNECTED
  -> Peer Discovery asks History to send a best-effort mini-backfill
  -> Peer Discovery does not need complete_history_backfill()
```

Assumption: `user_id` is formatted as `host:port`, for example:

```text
127.0.0.1:5002
```

## Message Types

Recovery messages are normal Distribution messages with JSON in `content`.

### `recover_request`

```json
{
  "type": "recover_request",
  "transfer_id": "uuid",
  "requester_id": "127.0.0.1:5002",
  "requester_host": "127.0.0.1",
  "requester_port": 5002,
  "have_vector_clock": {
    "127.0.0.1:5001": 3
  }
}
```

### `history_chunk`

```json
{
  "type": "history_chunk",
  "transfer_id": "uuid",
  "chunk_id": 1,
  "is_last": false,
  "messages": []
}
```

## Important Notes

- Vector clock is used as a recovery cursor.
- Target dedup is done by message ID.
- Multiple peers can send overlapping chunks safely.
- If target VC is `{}`, peers treat it like a new node and send all history
  they have.
- If target VC is `{"A": 3}`, peers send only messages from `A` with sequence
  greater than `3`.

## Current Limitation

`send_to_peer()` currently schedules async delivery and returns immediately.
So `complete_history_backfill()` means chunks were scheduled, not that the
target definitely ACKed every chunk.

If strict backfill is required, Distribution should provide something like:

```python
ok = node.send_to_peer_sync(host, port, msg)
```

or make `send_to_peer()` return a future that History can wait on.

## Test Commands

Run History tests:

```bash
.venv/bin/python -m pytest -s message-history/tests -q
```

Run full repo tests:

```bash
PYTHONPATH=message-history .venv/bin/python -m pytest -s -q
```
