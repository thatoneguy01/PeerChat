# Design: Message Encryption (Peer-to-Peer Chat)


| Field                  | Value                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------- |
| **Status**             | Draft                                                                                              |
| **Component**          | Security — Encryption                                                                              |
| **Language / runtime** | Python 3                                                                                           |
| **Related components** | Security (signatures), Peer discovery, Message distribution, Message history cache, User interface |


---

## 1. Executive summary

This document specifies **payload confidentiality** and **integrity/authenticity of ciphertext** for PeerChat’s distributed messages. Encryption is implemented as a **pure, testable library layer** that sits **below** message signing and **above** the raw transport bytes managed by discovery and distribution.

The chosen baseline is **symmetric authenticated encryption** using **AES-256-GCM** with a **group key** established out-of-band (shared network password or pre-shared key file). This matches the course constraint of **deferring complex asymmetric PKI** while still delivering a credible, implementable design with clear upgrade paths. That choice aligns especially well with **one room, broadcast-only** traffic: a single ciphertext can be read by every legitimate member who holds the same key.

---

## 2. Problem statement

### 2.0 PeerChat product model (normative)

The following constraints apply to the whole system and drive how encryption integrates with other components:

| Assumption | Description |
|------------|-------------|
| **Single global room** | There is exactly **one** logical chat room for the entire PeerChat deployment. There are no separate per-user or per-team rooms in v1. |
| **Decentralized control** | **No central service** is the authoritative owner of message content or of “who may communicate.” Peers relay, store, and verify cooperatively. Encryption assumes ciphertext may pass through **any** peer’s distribution/history path without trusting that peer with plaintext. |
| **Invite-only join** | A new node **cannot** self-attach to the network arbitrarily. Joining requires an **invitation** from a member already in the mesh. Discovery and admission logic are expected to enforce this; cryptography does not replace it, but **key delivery** (how the invitee learns the group key or password) should be specified jointly with the discovery/admission design (e.g. out-of-band link, in-person password, or encrypted invite payload in a later revision). |
| **Broadcast-only chat** | User-visible chat messages are **broadcast to every member** of the room. There is **no** separate v1 product requirement for pairwise (1:1) encrypted DMs; the encryption envelope is designed for **one payload, many recipients**. |

**Encryption implications:** a **shared group key** is appropriate; there is no per-recipient ciphertext variant for chat content. **AAD** still uses a logical `room_id` field for **versioning and domain separation** (see §6.3); for this product it is **fixed to `0`** unless the project later forks into multiple rooms.

### 2.1 Motivation

In a peer-to-peer chat network, messages traverse **untrusted networks** and may be stored or relayed by **peers you do not fully trust**. Without encryption, any observer with access to links or storage can read content. Without authenticated encryption (or an equivalent integrity mechanism on the ciphertext), an attacker can **flip bits** or **replace** ciphertext in ways that may confuse clients or harm users.

### 2.2 Scope of this design


| In scope                                                                                                        | Out of scope (explicit)                                                                     |
| --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Encrypting/decrypting **application message payloads** (chat text, attachments metadata, structured app events) | Full **identity-based** end-to-end encryption (E2EE) with per-recipient public keys and PKI |
| **Key derivation** from passwords / shared secrets                                                              | Legal/compliance certification (FIPS mode, common criteria)                                 |
| **Nonce and version discipline**, **AAD** binding for context                                                   | Anti-malware scanning of payloads                                                           |
| **Wire representation** and **versioning**                                                                      | Transport-layer TLS between peers (optional future; not required for this doc)              |
| **Integration contracts** for other teams                                                                       | UI visual design (only functional requirements)                                             |


---

## 3. System context

### 3.1 Logical placement

```
┌─────────────────────────────────────────────────────────────┐
│                         UI Layer                             │
│  (network password, errors, lock indicators, paste handling) │
└─────────────────────────────┬───────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│              Application / Chat Logic                        │
└─────────────────────────────┬───────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌───────────────┐    ┌────────────────┐    ┌───────────────────┐
│  Signatures   │◄──►│  Encryption    │◄──►│ History cache     │
│  (integrity/  │    │  (this doc)    │    │ (persist blobs)   │
│   authorship) │    └────────┬───────┘    └───────────────────┘
└───────┬───────┘             │
        │                     │
        └──────────┬──────────┘
                   ▼
        ┌──────────────────────┐
        │ Message distribution  │
        │ (fan-out, transport)  │
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ Peer discovery        │
        │ (membership, routing) │
        └──────────────────────┘
```

**Direction of data flow (send path):** plaintext → **encrypt** → (optional) **sign** on agreed material → **distribute** → network.

**Critical contract:** the **signatures** component and **encryption** component must agree on **which bytes are signed** and **whether signing happens before or after encryption**. Section 8.1 is normative for this integration.

### 3.2 Trust boundaries


| Boundary               | What is trusted                                               | What is not trusted                              |
| ---------------------- | ------------------------------------------------------------- | ------------------------------------------------ |
| **Network**            | Nothing                                                       | Links, Wi‑Fi, ISP, any relay                     |
| **Other peers’ nodes** | Correct protocol implementation only where explicitly assumed | Honesty, non-collusion, non-exfiltration of keys |
| **Local process**      | OS, Python runtime, user-supplied key material entry          | N/A                                              |


---

## 4. Goals and non-goals

### 4.1 Goals

1. **Confidentiality:** ciphertext must not reveal plaintext to parties without the active group key (under standard assumptions for AES-GCM).
2. **Integrity of ciphertext:** any modification of ciphertext (accidental or malicious) must fail decryption / verification with high probability.
3. **Context binding:** **associated authenticated data (AAD)** binds ciphertext to stable metadata to mitigate **context confusion** (e.g. accidental replay across a **different build**, testnet, or a hypothetical future multi-room fork). With **one global room**, AAD is not about “which room” in product terms; it anchors the blob to **PeerChat v1 semantics** and **envelope version**.
4. **Interoperability:** a **versioned**, **documented** on-wire format so distribution and cache teams can treat payloads as opaque byte strings with a small header.
5. **Testability:** deterministic test vectors and property tests for round-trips, failure modes, and nonce misuse guards.

### 4.2 Non-goals

1. **Post-compromise security** (self-healing after key theft) beyond a documented **manual key rotation** story.
2. **Per-message sender anonymity** at the cryptographic layer.
3. **Hiding traffic patterns** (message sizes, timing); that requires padding/traffic shaping and is out of scope.
4. **Replacing** the signatures team’s responsibilities; encryption does not prove authorship.
5. **Pairwise (1:1) chat** as a first-class product mode; v1 is **broadcast-only** (see §2.0).

---

## 5. Threat model

### 5.1 Actors


| Actor                        | Capabilities                                                           | Representative scenarios   |
| ---------------------------- | ---------------------------------------------------------------------- | -------------------------- |
| **Passive network observer** | Read all on-wire traffic                                               | Coffee shop Wi‑Fi sniffing |
| **Active network attacker**  | Drop, delay, replay, modify packets                                    | MITM on local segment      |
| **Malicious peer**           | Participates in protocol; may log traffic; may send malformed messages | Compromised classmate node |
| **Honest but curious peer**  | Follows protocol; tries to read others’ past messages if keys leak     | Shared laptop              |
| **Uninvited outsider**       | Not on the membership graph; may still see encrypted traffic if physically on the same LAN | Should not obtain plaintext without the group key; **must not** be able to self-join without an invite (admission is a **discovery/control-plane** requirement, not solved by AES alone) |


### 5.2 Security properties (intended)


| Property                                   | Provided by this design?           | Notes                                                                                                  |
| ------------------------------------------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| **Confidentiality of payload**             | Yes, for outsiders without the key | Members with the key can read                                                                          |
| **Integrity of encrypted payload**         | Yes (GCM authentication tag)       | Must not strip/replace tag                                                                             |
| **Authorship / non-repudiation**           | No                                 | Signatures team                                                                                        |
| **Protection against replay at app level** | Partial                            | GCM alone does not stop replay; **distribution/history** should use **monotonic ids / dedup** (see §9) |


### 5.3 Explicit limitations

- **Group key cryptography:** any holder of the current group key can decrypt **all** messages encrypted under that key, including those sent “to the room” in the past if they have ciphertext.
- **Revocation** of a member without changing the key is **not** cryptographically possible; **operational mitigation** is **key rotation** plus redistributing the new key only to remaining members (social/process layer for the course).
- **No PKI:** we cannot cryptographically bind a public key to a legal identity; any binding is **out-of-band** (e.g., in-person password exchange).

---

## 6. Cryptographic baseline

### 6.1 Algorithms


| Function                            | Algorithm                         | Parameters / sizes                                                                                                          |
| ----------------------------------- | --------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| **Authenticated encryption**        | **AES-256-GCM**                   | 256-bit key, 96-bit (12-byte) **random** nonce per encryption, 128-bit tag (default for GCM)                                |
| **Key derivation (password-based)** | **Argon2id**                      | Memory cost, iterations, parallelism tuned per device; output **32 bytes** (raw AES key)                                    |
| **Key derivation (binary secret)**  | **HKDF-SHA256**                   | `salt` = fixed domain separation string or random salt stored alongside encrypted blobs; `info` = ASCII `"peerchat-msg-v1"` |
| **Randomness**                      | `secrets.token_bytes` / OS CSPRNG | Never use `random` module for keys/nonces                                                                                   |


**Rationale:** AES-GCM is widely implemented, NIST-standardized, and available in mature Python libraries (`cryptography`). Argon2id is the modern default for password-based keys.

### 6.2 Nonce (IV) strategy

**Requirement:** For a given AES key, a GCM nonce **must never repeat**. Reuse is catastrophic for GCM (key stream recovery attacks).

**Chosen approach:** **Random 12-byte nonce per message**, generated with a CSPRNG.


| Approach                          | Pros                                                                                                           | Cons                                                                   |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| **Random 96-bit nonce**           | Simple API; no coordination; safe up to ~2^32 messages per key with negligible collision risk (birthday bound) | Theoretical birthday bound; requires good RNG                          |
| **Counter nonce**                 | No birthday issue if strictly monotonic                                                                        | Requires **persistent atomic counter** per key across restarts         |
| **Deterministic nonce from hash** | Reproducible                                                                                                   | Easy to foot-gun; generally discouraged for GCM unless expert-reviewed |


**Decision:** **Random 12-byte nonce** for v1.

### 6.3 Associated authenticated data (AAD)

GCM can authenticate additional bytes **without encrypting them**. Use AAD to bind ciphertext to stable, agreed metadata.

**PeerChat v1 (single room):** the product has **one** logical room. The AAD field **`room_id` is reserved and MUST be `0`** for all v1 messages. It remains in the layout so that **wire parsers and tests stay stable** if the class ever adds rooms later; it also gives a cheap constant for sanity checks.

**Decision:** Minimum AAD = UTF-8 encoding of `"peerchat\0"` + **big-endian uint32** `room_id` (always **0** in v1) + **big-endian uint32** `envelope_version`. Optional: `sender_id` **if** stable before encryption and visible on the wire in plaintext—only with cross-team agreement.

**Why:** With a single room, “wrong room” is not the main threat; AAD still binds the ciphertext to **this application’s message format** and **envelope version**, reducing ambiguity if blobs are ever reused across **different software versions** or **misconfigured clients**.

### 6.4 Canonical plaintext encoding

Before encryption, the **plaintext** MUST be a **well-defined byte sequence**:

- **Default:** UTF-8 encoding of JSON for structured messages **or** raw UTF-8 for plain chat strings—pick **one** project-wide; recommendation: **JSON envelope** with `{ "type": "...", "body": ... }` so attachments can evolve.

The encryption layer accepts **bytes** only; serialization is the caller’s responsibility.

---

## 7. On-wire format (normative)

### 7.1 Encrypted payload envelope (binary layout)

All multi-byte integers are **unsigned big-endian** unless stated otherwise.

```
Offset   Size     Field
------   ----     -----
0        1        magic = 0x4D  ('M' for message crypto layer; distinct from app magic)
1        1        envelope_version = 0x01
2        1        cipher_suite = 0x01  (AES-256-GCM)
3        1        flags (bit0=key_id_present, others reserved=0)
4        4        key_id (uint32, present if flags bit0 else zeros)
8        12       nonce
20       N        ciphertext || tag  (libs typically return ciphertext with tag appended;
                     document whether tag is trailing 16 bytes—MUST be consistent in code)
```

**Implementation note:** `cryptography.hazmat.primitives.ciphers.aead.AESGCM.encrypt` returns `ciphertext + tag` (16-byte tag). **Document the same in code comments** so other teams do not split incorrectly.

### 7.2 Optional JSON wrapper (interop mode)

If the project standardizes on JSON on the wire, the binary envelope may be **base64url-encoded** and embedded:

```json
{
  "crypto": {
    "suite": "AES_256_GCM_V1",
    "key_id": 0,
    "nonce_b64": "...",
    "aad_b64": "...",
    "payload_b64": "..."
  }
}
```

**Tradeoff:** ~33% size overhead vs raw binary. **Decision:** support **both** behind a `WireFormat` enum: `BINARY_V1` (preferred on LAN) and `JSON_V1` (debugging / human-readable logs). Distribution team picks default.

---

## 8. Cross-component integration

### 8.1 Signatures team — ordering and signed bytes

Two standard patterns:


| Pattern                     | Description                                                        | Typical use                                                                                            |
| --------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| **Encrypt-then-sign (EtS)** | Sign the **outer** blob that includes ciphertext + crypto metadata | Proves authorship of the exact bytes sent; hides plaintext from signer if signer signs only ciphertext |
| **Sign-then-encrypt (StE)** | Sign plaintext (or canonical message), then encrypt                | Signatures verifiable only after decrypt                                                               |


**Normative decision for PeerChat v1:** **Encrypt-then-sign**.

**Signed material (minimum):**  
`sign_bytes = SHA-256( crypto_envelope_binary || canonical_distribution_fields )`

Where `canonical_distribution_fields` is a **stable, documented byte concatenation** of fields the distribution layer considers authoritative (e.g. `message_id`, `sender_id`, `timestamp_unix_ms`). For v1, **`room_id` in that bundle SHOULD be omitted or fixed to `0`** since there is only one room—pick one convention and document it in the distribution/signatures joint spec.

**Broadcast note:** every member verifies the **same** signature over the **same** envelope bytes; there is no per-recipient ciphertext branch for chat content.

**Rationale:** EtS ties the signature to what actually traverses the network, reducing ambiguity if routing metadata changes between layers.

**Interface:**

- Encryption module exposes `crypto_envelope_binary` (or base64 form) as an output.
- Signing module accepts **opaque bytes** + returns signature fields.
- Verification order on receive: **verify signature** on the agreed `sign_bytes` **before** decrypt **only if** signature covers ciphertext; if the team signs plaintext only, order changes—**must not mix**; EtS + hash as above is the recommended default.

### 8.2 Peer discovery and invite-only admission

**Product coupling:** **Medium**. Discovery maintains **who is a member** of the single room and how to reach them. **Invite-only join** means admission is **not** open enrollment: a new peer should only appear after an **existing member** authorizes them. Encryption does not implement invites, but **group key delivery** must align with whatever the discovery/admission flow is (password shared when inviting, QR code, side channel, etc.).

**Coupling to crypto:** **Loose** for algorithms; **tight** for product security. Discovery may expose:

- **Member list** (or partial view) so distribution can **broadcast** to all live peers.
- Optional **non-secret** hints, e.g. current **`key_id`** epoch or Argon2 parameters used for password stretching (still avoid leaking secrets).

**Requirements:**

- Discovery and gossip payloads MUST NOT embed the **raw group key** in cleartext on the wire unless the course explicitly accepts that for demos; prefer **never** sending keys on discovery channels—use the **invite / out-of-band** path for key material instead.

**Optional extension:** advertise **`key_id`** of the current epoch so late joiners know they are stale.

### 8.3 Message distribution (broadcast)

**Coupling:** **Tight** for framing, **loose** for crypto internals.

PeerChat sends **one logical message** to **all current members**. Distribution therefore:

- Uses the **same** encrypted envelope for every recipient (no per-peer ciphertext variants for chat content).
- Relies on discovery (or cached membership) for the **fan-out target set**.

**Distribution responsibilities:**

- Treat `EncryptedEnvelope` as **opaque** after creation.
- Preserve **ordering** of bytes end-to-end (no re-encoding that changes base64 padding unless normalized everywhere).
- Implement **deduplication** using `message_id` (from app layer) to mitigate **replay** at the application layer (GCM does not prevent replay).

**Encryption responsibilities:**

- Provide **max_plaintext_size** guidance to avoid DoS (e.g. cap at 256 KiB for class project).
- Emit **constant-time** decrypt APIs where feasible for tag verification (library-dependent).

### 8.4 Message history cache

**Storage format:** persist **ciphertext envelopes** as received (plus any non-sensitive indexing fields required for retrieval).

**Key access for historical decrypt:**

- If history is **local-only**, the same **local key store** used for live messages suffices.
- If history is **shared across devices**, the course must accept **manual key export/import** or a **password** on each device.

**Migration:** `envelope_version` and `cipher_suite` fields allow future algorithms without breaking old records (old records remain decryptable only with old keys).

### 8.5 User interface

**Functional requirements:**

1. **Joining the network:** combine **invite acceptance** (product flow owned with discovery) with **key entry**: prompt for the **shared network password** or import **PSK file** (demo), unless the invite channel already delivered a derived key securely.
2. **Error states:** distinguish **bad password / wrong key**, **tampered message**, **unsupported version**, **missing key for key_id**.
3. **No key echo:** use password fields; clear sensitive buffers where practical (Python limits apply).
4. **Optional:** display **key epoch** (`key_id`) for power users debugging rotation.
5. **Broadcast UX:** no recipient picker for v1 chat; optional “sending to **N** peers” indicator driven by membership count from discovery.

**Non-requirements:** branding, themes, animations.

---

## 9. Key management

### 9.1 Key sources (v1)


| Mode              | Description                                    | Security notes                            |
| ----------------- | ---------------------------------------------- | ----------------------------------------- |
| **PSK file**      | 32-byte random key in a file excluded from git | Good for demos; poor UX                   |
| **Network password** | Shared secret string for the **only** room → Argon2id → 32-byte key | UX-friendly; strength depends on password; same secret for all invited members |


### 9.2 Key storage (local)

- **In memory:** `bytes` object for active group key; avoid logging.
- **On disk (optional):** encrypt local key cache with a **device key** derived from OS keychain if available; for the course, **in-memory only** may be acceptable.

### 9.3 Key rotation (v2 optional)

**Mechanism:**

1. Leader (or any agreed role) generates `new_key`, assigns `key_id = old_key_id + 1`.
2. Broadcast **rotation message** encrypted with **old key** containing `new_key` (for binary keys, wrap with AES-GCM using a **separate KWK** derived from old key via HKDF with `info="peerchat-rotate"`—avoid encrypting raw keys with the same nonce space; simplest course approach: embed `new_key` in a special **inner** structure with its own nonce).

**Minimum viable rotation for class:** **manual**: user enters new password; all users switch; old messages unreadable unless old key retained locally.

---

## 10. Alternatives considered (summary table)

### 10.1 Authenticated encryption suites


| Suite                              | Pros                                              | Cons                                                      | Verdict                                                           |
| ---------------------------------- | ------------------------------------------------- | --------------------------------------------------------- | ----------------------------------------------------------------- |
| **AES-256-GCM**                    | Hardware acceleration; ubiquitous                 | Fragile if nonce reused                                   | **Chosen**                                                        |
| **ChaCha20-Poly1305**              | Excellent SW performance; similar AEAD properties | Slightly less universal in older stacks                   | **Alternative** if `cryptography` / libsodium preferred uniformly |
| **Fernet** (`cryptography.fernet`) | Simple API                                        | Timestamp semantics, token overhead, less control for AAD | Not chosen for v1 (AAD + binary layout)                           |
| **AES-CBC + HMAC**                 | Legacy familiarity                                | Two keys, ordering pitfalls                               | Reject (prefer single AEAD primitive)                             |


### 10.2 Password-based KDF


| KDF                    | Pros                | Cons                              | Verdict                |
| ---------------------- | ------------------- | --------------------------------- | ---------------------- |
| **Argon2id**           | Memory-hard, modern | Params need tuning                | **Chosen**             |
| **scrypt**             | Memory-hard         | Older default                     | Acceptable alternative |
| **PBKDF2-HMAC-SHA256** | Simple              | weaker vs ASIC for weak passwords | Legacy fallback only   |


### 10.3 Asymmetric E2EE (Signal-style)


| Approach                 | Pros                               | Cons                          | Verdict                            |
| ------------------------ | ---------------------------------- | ----------------------------- | ---------------------------------- |
| **Double Ratchet / MLS** | Strong forward secrecy, membership | Complexity, PKI/session state | **Out of scope** for v1 per course |


---

## 11. Module design (implementation-facing)

### 11.1 Suggested package layout

```
peerchat/
  crypto/
    __init__.py
    envelope.py       # build/parse binary envelope
    keys.py           # Argon2id, HKDF helpers
    encrypt.py        # AESGCM encrypt/decrypt
    errors.py         # exception taxonomy
    constants.py      # sizes, magic bytes, suite ids
```

### 11.2 Public API (sketch)

```python
# keys.py
def derive_key_from_password(password: str, *, salt: bytes) -> bytes: ...
def generate_salt() -> bytes: ...  # 16 bytes CSPRNG

# encrypt.py
def encrypt_message(
    *,
    key: bytes,
    key_id: int,
    room_id: int,  # PeerChat v1: pass 0 (single global room); reserved for future use
    plaintext: bytes,
) -> bytes: ...  # returns full crypto envelope binary

def decrypt_message(
    *,
    keyring: Mapping[int, bytes],  # key_id -> key
    room_id: int,  # PeerChat v1: pass 0; must match AAD used at encrypt time
    envelope: bytes,
) -> bytes: ...  # returns plaintext or raises

# errors.py
class CryptoError(Exception): ...
class UnknownSuiteError(CryptoError): ...
class DecryptFailedError(CryptoError): ...  # bad key or tamper
class UnsupportedEnvelopeVersionError(CryptoError): ...
```

**Thread safety:** AESGCM objects from `cryptography` are reusable; prefer **one encrypt routine that constructs AEAD per call** for simplicity.

### 11.3 Configuration surface


| Parameter            | Example | Owner                               |
| -------------------- | ------- | ----------------------------------- |
| Argon2 `time_cost`   | 3       | Encryption team (document defaults) |
| Argon2 `memory_kib`  | 65536   | Encryption team                     |
| Argon2 `parallelism` | 1       | Encryption team                     |
| Max plaintext bytes  | 262144  | Cross-team with distribution        |


---

## 12. Error handling and observability

### 12.1 Exception taxonomy


| Error                             | User-visible message (UI)                                                | Log / metrics              |
| --------------------------------- | ------------------------------------------------------------------------ | -------------------------- |
| `DecryptFailedError`              | “Message couldn’t be verified. It may be corrupted or encrypted with a different key.” | Count; no payload logging  |
| `UnknownSuiteError`               | “This message uses unsupported encryption.”                              | Include `cipher_suite` int |
| `UnsupportedEnvelopeVersionError` | “Update your client.”                                                    | Include version byte       |


**Never** log keys, passwords, nonces, or plaintext.

### 12.2 Metrics (optional for course)

- `crypto_encrypt_success_total`
- `crypto_decrypt_success_total`
- `crypto_decrypt_failure_total{reason=tamper|bad_key|version}`

---

## 13. Performance and limits

- **Target latency:** encrypt/decrypt < 1 ms per message for ≤ 64 KiB plaintext on a laptop (informal; not a hard SLO for the class).
- **DoS limits:** reject envelopes larger than **1 MiB** at the parser before allocation-heavy paths.
- **Parallelism:** encryption is embarrassingly parallel; batch history decrypt may use `concurrent.futures` with a cap.

---

## 14. Testing strategy

### 14.1 Unit tests

- Round-trip: random plaintext sizes ∈ {0, 1, 1024, max-1}.
- Wrong key: decrypt fails with `DecryptFailedError`.
- Tamper: flip one bit in ciphertext or tag; must fail.
- **Mismatched AAD:** encrypt with `room_id=0` but decrypt with a different `room_id` passed into the API: must fail if AAD is enforced correctly (guards coding mistakes even though v1 is always `0`).
- **Golden vectors:** fixed key/nonce/ plaintext → known ciphertext+tag (checked into test data).

### 14.2 Property tests (Hypothesis optional)

- For random plaintext, decrypt(encrypt(x)) == x.

### 14.3 Integration tests (with stubs)

- Fake “distribution” that serializes/deserializes envelope unchanged.
- Contract test: signature team’s mock verifies `SHA-256(envelope || fields)` matches implementation.

---

## 15. Versioning and compatibility

- **Envelope version** byte increments only on **breaking** layout changes.
- **Cipher suite** byte adds new algorithms without bumping envelope version if parser supports suite dispatch.
- Clients MUST reject **unknown** envelope versions with a clear error (no partial decrypt).

---

## 16. Rollout plan (suggested)

1. **Phase 0:** merge library with **no-op** mode behind feature flag (optional).
2. **Phase 1:** encrypt **new** messages only; history shows “legacy cleartext” if any (may be none in greenfield).
3. **Phase 2:** UI password; persist salt locally (scoped by install or invite id if multiple networks are tested on one machine).
4. **Phase 3:** optional key rotation / `key_id` support.

---

## 17. Open questions (for class coordination)

1. **Exact canonical bytes** for signature hashing (field order, endianness, UTF-8 normalization).
2. **Default wire format:** binary vs base64 JSON for the whole project.
3. **Invite + key flow:** how the inviter conveys **password / PSK / salt** to the invitee without a central server (this is the main residual product-security question for symmetric crypto).
4. **Attachment policy:** encrypt file bytes inline vs separate blob store with same envelope.
5. **Leader / operator model** for automated key rotation (if any)—harder in a purely decentralized design; manual rotation may be the v1 default.

---

## 18. References (informative)

- NIST SP 800-38D (GCM mode)
- RFC 5869 (HKDF)
- Argon2 specification (RFC 9106)
- `cryptography` documentation: `AESGCM`

---

## Appendix A: Example sequence (send)

1. UI collects user message `m`.
2. App forms `plaintext = canonical_encode(m)`.
3. `envelope = encrypt_message(key=group_key, key_id=current, room_id=0, plaintext=plaintext)` (v1 single room).
4. `sig_fields = signatures.sign(hash(envelope || dist_canonical))`.
5. Distribution publishes `{envelope, sig_fields, dist_canonical_fields}`.

## Appendix B: Example sequence (receive)

1. Distribution delivers message record.
2. Signatures verifies using agreed `sign_bytes`.
3. `plaintext = decrypt_message(keyring=keys, room_id=0, envelope=envelope)`.
4. App decodes plaintext; UI renders.

---

## Document history


| Version | Date       | Author                        | Changes             |
| ------- | ---------- | ----------------------------- | ------------------- |
| 0.1     | 2026-05-11 | Security — Encryption (draft) | Initial full design |


