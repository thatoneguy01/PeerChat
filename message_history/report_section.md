# Message History, Recovery, and Snapshot Module

## 1. Introduction

The message history, recovery, and snapshot module was built to improve
reliability in the distributed chat system. Live message delivery is handled by
the transport layer, but a node can still miss messages when it joins late,
goes offline, reconnects, or restarts. This module solves that problem by
storing chat messages locally on each node and allowing nodes to recover missing
history from peers.

The goal is to make sure that a node can:

- Save received messages locally.
- Detect and skip duplicate messages.
- Recover missed messages after joining or reconnecting.
- Stream missing history to peers in small chunks.
- Compact older history using snapshots.
- Detect damaged local storage and trigger a fuller recovery when needed.

## 2. Design Overview

The module is based on five main design ideas:

- **Local persistence:** each node stores the messages it receives.
- **Vector-clock recovery:** each node uses its persisted vector clock as a
  recovery cursor.
- **Duplicate prevention:** message IDs are used to avoid saving the same
  message more than once.
- **Chunked transfer:** missed history is sent in smaller batches instead of one
  large response.
- **Snapshot compaction:** older logs are compressed so the active log does not
  grow forever.

Recovery requests and history chunks are carried as JSON payloads inside the
normal message envelope. Recovery messages are handled internally and are not
displayed as normal chat messages.

### 2.1 Local Storage

Each node keeps an append-only message log on disk. When a normal chat message
arrives, it is saved to the log and the local indexes are updated.

The main storage files are:

```text
message_history/runtime/<port>/logs/active.log.jsonl
message_history/runtime/<port>/index/message_id.index
message_history/runtime/<port>/index/sender_seq.index
message_history/runtime/<port>/index/latest_vector_clock.json
message_history/runtime/<port>/index/recovery_state.json
message_history/runtime/<port>/snapshots/
```

The indexes support:

- Fast duplicate checking by message ID.
- Tracking sender sequence numbers.
- Storing the latest vector clock for recovery.
- Remembering whether damaged storage requires a fuller recovery.

This keeps normal message writes simple while still allowing efficient recovery
later. Index files are written through temporary files before replacing the old
version, which reduces corruption risk if the process stops during an update.

### 2.2 Recovery Process

When a node joins or comes back online, it can send a recovery request to active
peers. The request includes the node's latest persisted vector clock, which
tells other peers how much history it already has from each sender. Peers
compare that cursor with their own stored messages and send back messages whose
sender sequence is higher than the requester's known sequence.

Recovery asks all active peers instead of randomly choosing a small subset. This
is safer because no single peer is guaranteed to have complete history. A target
node may receive duplicate messages from multiple peers, but duplicates are
safely ignored by message ID.

If local storage damage is detected, such as unreadable snapshots, snapshot
sequence gaps, invalid active-log lines, or sender sequence gaps, the store does
not blindly trust the latest cursor. It can force a fuller recovery by reporting
an empty vector clock, which asks peers to resend more complete history.

### 2.3 Chunked History Transfer

Missed messages are sent in chunks instead of a single large payload. This keeps
recovery manageable when a node has missed many messages.

The recovery flow is:

```text
recovering node sends recover_request
  -> provider reads the target vector clock
  -> provider finds messages the target is missing
  -> provider splits those messages into chunks
  -> provider sends history_chunk messages directly to the target
  -> target saves each chunk
  -> target drops duplicate messages by message ID
```

The default chunk size is `100`. Chunks are sent directly to the recovering peer
instead of being broadcast to all peers.

### 2.4 Snapshot Compaction and Recovery

Snapshots are used to keep the active message log from growing indefinitely. A
snapshot stores older messages in a compressed file and records metadata such as:

- Snapshot ID.
- Creation time.
- Message count.
- Covered vector clock.
- Checksum.
- Data file name.

Snapshot files use this format:

```text
snapshot-0001.jsonl.gz
snapshot-0001.meta.json
```

The checksum helps verify that a snapshot file is still valid before it is read.
After a snapshot is safely created, the active log can be compacted. Recovery
still works because the store reads from both snapshots and the active log.

If snapshot damage is detected, the module marks recovery state so the next
recovery can request a fuller history. After enough messages are recovered, the
module can rebuild snapshots from readable messages, rewrite active-log
remaining messages, rebuild indexes, and update the latest vector clock.

### 2.5 Summary of Message History Recovery Flow

```text
Node starts or reconnects
  -> LocalMessageStore loads logs, snapshots, and indexes
  -> LocalMessageStore repairs indexes from readable storage
  -> LocalMessageStore decides whether the vector clock is safe to use
  -> History sends recovery request to active peers
  -> Peers build missing-history chunks from their local storage
  -> Peers send history chunks directly to the recovering node
  -> Recovering node saves messages with deduplication
  -> Snapshot rebuild runs if storage damage was detected and recovery filled gaps
  -> Latest vector clock is updated
```

## 3. Module Integration

The history module exposes a small service wrapper for the main application:

```python
history = HistoryService(node=node, host=node.host, port=node.port)
history.start()
```

Incoming messages should be passed through History first:

```python
if history.handle_message(msg).get("handled"):
    return
```

If the message is a recovery request or history chunk, History handles it
internally. Otherwise, the message is treated as a normal chat message and saved
to local storage.

The main public API is:

```python
history.start()
history.handle_message(msg)
history.request_missing_history()
history.get_recent_messages(limit=100)
```

## 4. Team-Based Tests

### 4.1 New Node Joins

Two existing nodes exchange messages. A third node joins later and requests
history from active peers.

Observed result:

- The new node receives previous messages.
- Duplicate messages are skipped.
- The node can continue receiving live messages after recovery.

### 4.2 Offline Node Returns

One node goes offline while the other nodes continue sending messages. When it
returns, it sends its last known vector clock and receives the messages it
missed.

Observed result:

- The returning node catches up correctly.
- Peers do not need to resend messages the node already has.
- Live message delivery can continue after recovery.

### 4.3 Snapshot-Based Recovery

Messages are sent until snapshots are created. A node then recovers history from
both snapshot files and the active log.

Observed result:

- Compacted messages remain recoverable.
- Snapshot data is not displayed as live chat.
- Recovery still works after active-log compaction.

### 4.4 Offline Node Returns With Partial Local Corruption

One node goes offline while other peers continue sending messages. During the
offline period, part of the node's local history is damaged or missing. For
example, the node may still have snapshots `0001`, `0002`, and `0005`, but
snapshots `0003` and `0004` are missing.

When the node returns, it validates local storage before trusting its recovery
cursor. If it detects snapshot gaps, unreadable snapshot data, invalid log
lines, or sender sequence gaps, it forces a fuller recovery instead of relying
only on the latest vector clock.

Observed result:

- The node detects damaged or missing local history.
- Missing history can be requested again from peers.
- Existing messages are not duplicated.
- Indexes and vector clocks are rebuilt from readable/recovered data.
- Snapshot rebuild can restore a consistent snapshot layout.

## 5. Key Design Decisions

The main design decisions were:

- Use vector clocks to identify missing messages without sending full message
  lists.
- Use message IDs to make recovery idempotent and safe during retries.
- Request history from all active peers because one peer may not have complete
  history.
- Send recovery chunks directly to the recovering node.
- Use snapshots to control active-log growth while preserving recoverable
  history.
- Separate recovery traffic from normal chat messages.
- Expose `HistoryService` so the main application can use History without
  directly calling low-level storage classes.

## 6. Limitations

- If all active peers are also missing a message, History cannot recover that
  message from the network.
- Strict confirmation that every chunk was received requires the send layer to
  expose an ACK result or future.
- If files are manually deleted outside the storage API, the module can detect
  many damage cases, but full correctness depends on recovering the missing data
  from another peer.
- If encryption keys change after restart, the stored message may still exist
  but may not be readable by the UI.

## 7. Conclusion

This module adds reliable history recovery to the distributed chat system. Each
node stores messages locally, tracks its latest vector clock, and can recover
missed messages from active peers. The use of chunked transfer, duplicate
detection, snapshot compaction, and index repair makes the design practical for
nodes that join late, disconnect, restart, or experience partial local storage
damage.
