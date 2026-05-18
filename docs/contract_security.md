# Integration Contract — Security Team

**Owner on our side:** Bhuvana (Message Distribution POC)
**Audience:** Security team POC
**Status:** Draft for sign-off, 2026-05-12
**Relevant MD code:** `distribution/message.py`, `distribution/broadcast_node.py`

---

## What Message Distribution needs from you

Two functions Message Distribution calls on the real send/receive paths: `sign(msg)` before sending, and `verify(msg)` before ACK, deduplication, delivery, or forwarding.

We have left a `signature: str` field on `Message` (see `message.py`). Security populates it; MD carries it across the wire and checks it on receive.

## The interface

You should ship a module `security/security.py` exposing:

```python
def sign(msg: Message) -> Message:
    """Fill msg.signature. Returns the same message for chaining."""
    ...

def verify(msg: Message) -> bool:
    """Return True if signature is valid for the given message. No side effects."""
    ...

# Optional for E2E encryption:
def encrypt_payload(msg: Message, recipient_pubkey: bytes) -> Message: ...
def decrypt_payload(msg: Message, own_privkey: bytes) -> Message: ...
```

## Call order on send

The originator (typically the UI) builds the message and hands it to MD:

```python
msg = Message(content=text, sender=node.address)
node.broadcast(msg)
```

MD calls `security.sign(msg)` before sending. It then fills `vector_clock` and handles forwarding.

**Important — fields that change after signing:**

`BroadcastNode.broadcast()` fills `msg.vector_clock` **after** MD signs. `msg.ttl` is decremented on every forward hop. If you include either in the signed payload, the signature will be invalid at every downstream peer.

**You must exclude `ttl` and `vector_clock` from the signed canonical form.**

Sign these stable fields only:

- `id`
- `sender`
- `timestamp`
- `content`

(Canonicalize over the serialized form with `signature=""` as well — otherwise the signature would sign itself.)

## Call order on receive

Each peer's BroadcastNode calls `security.verify(msg)` before ACK, deduplication, delivery, or forwarding. Messages with missing/invalid signatures or missing sender public keys are rejected before `node.on_message` fires.

## What you can assume about us

- `Message.signature` is a string. Put whatever you want in it (hex, base64, JSON-encoded structure). We don't parse it.
- The full `Message` is serialized via `dataclasses.asdict → json.dumps`. Document exactly how you canonicalize the signed subset.
- We will not modify `id`, `sender`, `timestamp`, or `content` in flight. These are safe to sign.
- We **will** mutate `vector_clock` on send (inside `broadcast()`) and `ttl` on every hop. Do not sign these.

## The TTL + VC gotcha — summary

Two `Message` fields change during transit and therefore cannot appear in your signed canonical form:

| Field | Who changes it | When |
|---|---|---|
| `ttl` | MD `_forward` | Every hop (decremented) |
| `vector_clock` | MD `_do_broadcast` | After MD signs, before send |

Anything else is stable and signable.

## Key distribution

Out of scope for MD. You decide how public keys / shared secrets get to peers. If your solution requires MD to piggyback keys on broadcast messages, that's a wire-format change we'd want to review.

## Open questions we need you to confirm by EOD 2026-05-12

- **Q1:** Confirm `ttl` and `vector_clock` excluded from signed canonical form. Yes/no.
- **Q2:** Resolved: MD drops unverified messages pre-delivery.
- **Q3:** Should we add a `node.stamp(msg)` helper that fills vector_clock up front, so you can sign-with-VC if you want a stronger binding? Default: no.
