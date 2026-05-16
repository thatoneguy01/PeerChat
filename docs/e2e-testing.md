# End-to-end testing (Security + Distribution)

Code-driven E2E tests run **multiple real peer processes on one machine**, similar in spirit to Docker Compose: each peer is an isolated OS process with its own WebSocket port and HTTP control API.

## Prerequisites

```bash
cd PeerChat
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Quick start (recommended)

Run three peers, send one hybrid-encrypted message, assert all three decrypt it:

```bash
python -m e2e.cli run --peers 3
```

Expected output ends with `E2E mesh run PASSED` and `Delivered to 3/3 peers`.

## Pytest E2E suite

```bash
# E2E only (spawns subprocess peers)
pytest -m e2e -v

# Unit + integration (no subprocess peers)
pytest -m "not e2e" -q

# Via Makefile
make test-e2e
make test-unit
make test
```

## In-process tests (faster)

No extra processes — mesh runs inside pytest:

```bash
pytest tests/test_chat_session_integration.py -v
pytest tests/test_security_encryption.py tests/test_crypto_encrypt.py -v
```

## Encrypted demo (distribution only)

```bash
python demo.py --encrypted-only
```

## Run peers manually

**Terminal 1 — peer A**

```bash
python -m e2e.peer_worker --port 5201 --control-port 15201
```

**Terminal 2 — peer B**

```bash
python -m e2e.peer_worker --port 5202 --control-port 15202
```

**Terminal 3 — orchestrator (Python shell or script)**

Use `e2e.mesh.PeerMesh` or run the CLI (starts and stops peers for you):

```bash
python -m e2e.cli run --peers 3 --base-ws-port 5201
```

### Control HTTP API (per peer)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Liveness + `address` |
| GET | `/pubkey` | RSA public key (base64 PEM) |
| POST | `/roster` | `{"peers": {"user_id": "pem_b64", ...}}` |
| POST | `/peers` | `{"peers": [{"host","port"}, ...]}` for WS mesh |
| POST | `/send` | `{"plaintext": "hello"}` |
| GET | `/messages` | Decrypted inbox |
| POST | `/clear` | Clear inbox |
| POST | `/shutdown` | Stop peer |

Example:

```bash
curl -s http://127.0.0.1:15201/health | python -m json.tool
curl -s http://127.0.0.1:15202/messages | python -m json.tool
```

## Default ports

| Peers | WebSocket | Control HTTP |
|-------|-----------|----------------|
| CLI / pytest `local_mesh` | 5301–5303 | 15301–15303 |
| `e2e.cli run` default | 5201–5203 | 15201–15203 |
| Docker Compose | 5201–5203 | 15201–15203 (published) |

## Docker Compose (optional)

Runs three peer containers; run the CLI from the host against published ports:

```bash
docker compose -f docker-compose.e2e.yml up --build -d
python -m e2e.cli run --peers 3 --base-ws-port 5201
docker compose -f docker-compose.e2e.yml down
```

## What is being tested

1. **Hybrid encryption** — RSA-OAEP wraps AES-256-GCM per recipient (`pcrsa-h1` wire in `Message.content`).
2. **Signatures** — RSA-PSS over `id`, `sender`, `timestamp`, `content`.
3. **Distribution** — real `BroadcastNode` WebSocket fan-out and dedup.
4. **Pipeline** — `prepare_outgoing` → `broadcast` → `open_incoming` on each peer.

## CI suggestion

- Every push: `pytest -m "not e2e"`
- PR / nightly: `pytest -m e2e`

## Related docs

- [design-message-encryption.md](design-message-encryption.md) — algorithms and wire format
- [contract_security.md](contract_security.md) — Distribution ↔ Security contract
