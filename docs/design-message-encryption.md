# Design: Message Encryption (Peer-to-Peer Chat)


| Field                  | Value                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------- |
| **Status**             | **v1 — team-aligned draft** (2026-05-15)                                                           |
| **Component**          | Security — Encryption                                                                              |
| **Language / runtime** | Python 3 (`cryptography` hazmat RSA + AES-GCM)                                                     |
| **Owners**             | **Ashish** — payload encryption + wire format; **Brandon** — RSA private key storage; **Miguel** — RSA signatures; **Himanshu** — pubkey distribution via Peer Discovery |
| **Related components** | Message distribution (transport only), Message history cache, UI                                   |


---

## 1. Executive summary (v1 — normative)

PeerChat v1 confidentiality uses **asymmetric cryptography**, not a shared room password or long-lived AES group key.

**Team decisions (Discord, 2026-05-11 — 2026-05-15):**

1. Each peer generates an **RSA keypair** at startup (only if one does not already exist).
2. The **private key** stays in-process and is stored via **Brandon’s `InMemoryKeyStore`** (and optional OS keyring persistence). There are **no stored AES group keys**.
3. **Peer Discovery** publishes each member’s **public key PEM** in `JOIN_RESPONSE` / gossip (`user_id`, address, `public_key`). Discovery does not implement chat encryption; it supplies the pubkey roster.
4. **Bootstrap:** a new peer learns one seed peer’s `(host, port, pubkey)` out-of-band, sends an encrypted hello, and receives the roster (Ryan’s “seed as trust anchor” model).
5. **Chat:** logical messages are **broadcast to the room**, but confidentiality is **per-recipient**: the sender builds **one hybrid ciphertext per member** and places them in `Message.content` (see §7).
6. **Message Distribution** does not encrypt; it carries opaque `content` + `signature` (Bhuvana). **Miguel** implements **RSA signatures** on stable fields (`id`, `sender`, `timestamp`, `content`) — authorship is separate from confidentiality.
7. **v1 algorithm (chosen):** **Hybrid RSA-OAEP + AES-256-GCM** — RSA wraps a random per-message AES key; the existing `crypto/` package encrypts the body. Pure RSA-only is supported for tiny payloads but is **not** the default for chat.

**Send path:** plaintext → **encrypt (per recipient)** → **sign** → **broadcast** → network.

**Receive path:** verify signature → **decrypt own box** → UI.

---

## 2. Approach comparison (pros & cons)

Ryan’s direction emphasized **RSA for message confidentiality**. Two concrete options:

### Option A — RSA-only (encrypt whole message with RSA-OAEP)

| Pros | Cons |
|------|------|
| Simple mental model; matches “just use asymmetric” | **~190 byte max** per message with RSA-2048 OAEP-SHA256 — unusable for normal chat |
| No symmetric layer to explain | **O(recipients)** full RSA ops on large payloads impossible |
| Fewer moving parts | Slow and large ciphertexts even for short text |

**Verdict:** acceptable for **control-plane hellos / key material**; **rejected as default for chat**.

### Option B — Hybrid RSA + AES-GCM (v1 normative)

| Pros | Cons |
|------|------|
| **Unlimited chat length** (within `MAX_PLAINTEXT_BYTES`) | Slightly more implementation surface |
| Fast bulk encryption (AES-GCM) | Each recipient still needs one RSA operation per message |
| Reuses tested `crypto/` AES envelope | Wire `content` larger (N boxes per broadcast) |
| Standard industry pattern (TLS-like) | Ephemeral AES keys not forward-secret across messages |
| Fits “one encryption per recipient” (Himanshu) | Team must agree on JSON wire format in `content` |

**Verdict:** **chosen for v1 chat**.

### Option C — Symmetric group key (superseded v0 draft)

| Pros | Cons |
|------|------|
| One ciphertext for whole room; smallest broadcast | **No per-member exclusion** — everyone with password reads everything |
| Very fast | Conflicts with **pubkey bootstrap** direction |
| Easy demo | Ryan / team explicitly moving away from shared AES keys |

**Verdict:** documented in **Appendix C** only; **not** v1.

---

## 3. Problem statement

### 3.1 Product model

| Assumption | Encryption implication |
|------------|-------------------------|
| **Single global room** | One logical broadcast; `Message.content` may contain **multiple recipient boxes** |
| **Invite-only join** | Admission + pubkey bootstrap via seed peer; crypto does not replace invite policy |
| **Decentralized relay** | Relays see ciphertext boxes but should not read plaintext without a private key |
| **Members trusted after join** | Course spec may treat members as honest; crypto still protects **network observers** |

### 3.2 Scope

| In scope | Out of scope |
|----------|--------------|
| `encrypt_for_peer` / `decrypt_from_peer` (hybrid) | Full Signal/MLS double ratchet |
| `encrypt_broadcast_content` / `decrypt_broadcast_content` | TPM integration (stretch; Ryan mentioned) |
| Wire format in `Message.content` | Miguel’s RSA **signatures** (separate module) |
| Integration contract with Distribution | TLS between peers |

---

## 4. System context

```
┌──────────────┐     pubkeys      ┌─────────────────────┐
│ Peer         │◄────────────────│ Peer Discovery       │
│ Discovery    │                 │ (Himanshu)           │
└──────┬───────┘                 └─────────────────────┘
       │ roster {user_id → pubkey_pem}
       ▼
┌──────────────┐   private key   ┌─────────────────────┐
│ Encryption   │◄───────────────│ Key storage          │
│ (Ashish)     │                 │ (Brandon)            │
└──────┬───────┘                 └─────────────────────┘
       │ ciphertext in content
       ▼
┌──────────────┐   signature     ┌─────────────────────┐
│ Signatures   │◄───────────────│ same private key     │
│ (Miguel)     │                 │ store                │
└──────┬───────┘                 └─────────────────────┘
       ▼
┌─────────────────────┐
│ Message Distribution │  (Bhuvana — opaque relay)
└─────────────────────┘
```

---

## 5. Cryptographic baseline (v1)

| Function | Algorithm | Notes |
|----------|-----------|-------|
| **Asymmetric wrap** | **RSA-2048**, **OAEP** (SHA-256, MGF1-SHA256) | Wraps 32-byte ephemeral AES key |
| **Bulk encryption** | **AES-256-GCM** | Via `crypto/encrypt.py`; random 12-byte nonce |
| **Signatures** | **RSA** (Miguel) | Over stable message fields; not in this module |
| **Private key storage** | PEM in **Brandon’s store** | Generate once; load from keyring on restart |

Library: [`cryptography` RSA](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/rsa/).

---

## 6. Per-recipient hybrid blob (binary)

```
Offset   Size     Field
------   ----     -----
0        4        magic = b"PC\x02\x01"
4        2        wrapped_key_len (uint16 BE)
6        N        RSA-OAEP ciphertext of 32-byte AES key
6+N      M        AES-GCM envelope (see crypto/ binary layout §7.1 legacy doc)
```

`cryptography` AESGCM output = `ciphertext || 16-byte tag`.

---

## 7. Broadcast wire format (`Message.content`)

For a room message, `content` is a **JSON string**:

```json
{
  "v": "pcrsa-h1",
  "boxes": {
    "alice": "<urlsafe-base64 hybrid blob>",
    "bob": "<urlsafe-base64 hybrid blob>"
  }
}
```

- Each member decrypts **`boxes[own_user_id]`** only.
- Distribution treats `content` as an opaque string (after JSON serialization in the app).
- **Legacy:** `pc1:` prefix was the superseded symmetric group-key format.

---

## 8. Cross-component integration

### 8.1 Signatures (Miguel) — ordering

**Normative v1:** **Encrypt-then-sign** on the wire the network sees.

1. Build encrypted `content` (JSON boxes).
2. Set `Message.content`.
3. `sign(msg)` over stable fields: `id`, `sender`, `timestamp`, `content` (with `signature=""` in canonical form).
4. `broadcast(msg)`.

**Do not sign** `ttl` or `vector_clock` (Distribution mutates them). Flag signing `vector_clock` as a future improvement (Miguel / Bhuvana thread).

### 8.2 Peer Discovery (Himanshu)

- Publishes **`public_key` PEM** per member.
- Encrypted **JOIN_REQUEST** / control messages use the same `encrypt_for_peer` primitive (recipient = seed pubkey).
- **Heartbeats:** cleartext (team decision).

### 8.3 Message Distribution (Bhuvana)

```python
# Originator (UI / demo)
content = encrypt_broadcast_content(
    plaintext=user_text,
    recipient_pubkeys=discovery.roster_pubkeys(),  # {user_id: pem}
)
msg = Message(content=content, sender=node.address)
msg = security.sign(msg)   # Miguel
node.broadcast(msg)
```

```python
# Receiver
def on_message(msg: Message) -> None:
    if not security.verify(msg):
        return
    text = decrypt_broadcast_content(
        content=msg.content,
        own_user_id=self.user_id,
        private_key_pem=key_store.get_private_key(),
    )
```

### 8.4 Key storage (Brandon)

- Store **RSA private key PEM** (`bytes`), not AES group keys.
- On restart: reload from keyring or prompt regen if missing.
- Miguel and Ashish both call `get_private_key()` / `get_public_key_pem()`.

### 8.5 Public API (`security/encryption.py`)

```python
def encrypt_for_peer(*, plaintext: bytes, recipient_public_key_pem: bytes) -> bytes: ...
def decrypt_from_peer(*, ciphertext: bytes, private_key_pem: bytes) -> bytes: ...
def encrypt_broadcast_content(*, plaintext: str, recipient_pubkeys: Mapping[str, bytes]) -> str: ...
def decrypt_broadcast_content(*, content: str, own_user_id: str, private_key_pem: bytes) -> str: ...
def get_public_key_pem(private_key_pem: bytes) -> bytes: ...
```

`security/rsa_keys.py`: `generate_rsa_keypair() -> (private_pem, public_pem)`.

---

## 9. Module layout

```
security/
  encryption.py      # hybrid per-peer + broadcast wire (v1)
  rsa_keys.py          # PEM keypair helpers
  key_storage.py       # Brandon — in-memory private key
  keyring_key_storage.py
  message_integrity.py # Miguel — sign / verify (in progress)

crypto/                # symmetric engine (AES-GCM envelope) — used inside hybrid
  encrypt.py
  envelope.py
  ...
```

---

## 10. Error handling

| Error | Meaning |
|-------|---------|
| `EncryptionError` | Bad inputs, RSA-only payload too large |
| `DecryptPayloadError` | Wrong key, tamper, missing box, bad version |

Never log private keys, PEM, or decrypted plaintext.

---

## 11. Testing

| Suite | Path |
|-------|------|
| Hybrid round-trip | `tests/test_security_encryption.py` |
| AES envelope (inner layer) | `tests/test_crypto_encrypt.py` |
| Key storage | `tests/test_key_storage.py` |

---

## 12. Rollout plan

| Phase | Deliverable |
|-------|----------------|
| **0** | Design alignment (this doc) + `security/encryption.py` |
| **1** | Discovery publishes pubkeys; demo encrypt → sign → broadcast |
| **2** | UI: show decrypt errors; persist key via keyring |
| **3** | History stores opaque `content`; optional vector_clock signing |

---

## 13. Open questions

1. Exact PEM format on the wire in Discovery (SPKI vs PKCS#8 public PEM) — standardize one.
2. Whether `content` JSON is compressed for large rooms.
3. TPM / secure enclave for private key (Ryan stretch).
4. Including `sender_id` in AES AAD for hybrid inner envelope.

---

## Appendix A — Send / receive sequence

**Send**

1. UI collects text.
2. Load roster pubkeys from Discovery.
3. `content = encrypt_broadcast_content(plaintext, recipient_pubkeys)`.
4. `msg = Message(content=content, ...)`.
5. `msg = sign(msg)`.
6. `broadcast(msg)`.

**Receive**

1. `verify(msg)`.
2. `text = decrypt_broadcast_content(msg.content, own_user_id, private_key_pem)`.
3. UI render.

---

## Appendix B — Superseded v0 (symmetric group key)

The 2026-05-11 draft specified **AES-256-GCM with a shared group password** and a single ciphertext per room (`pc1:` wire prefix). That approach is **retained in `crypto/`** for the hybrid inner layer but is **not** the v1 confidentiality model for chat.

See git history / `crypto/wire.py` (`pc1:`) if needed for migration tests.

---

## Document history

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1 | 2026-05-11 | Ashish | Initial symmetric group-key design |
| **1.0** | **2026-05-15** | **Ashish** | **v1 team alignment: RSA pubkey bootstrap, per-recipient hybrid, pros/cons, implementation** |
