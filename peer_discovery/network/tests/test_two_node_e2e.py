"""End-to-end integration test for two nodes bootstrapping.

After the consolidation, both nodes route their wire traffic through a
``FakeBroadcastNode`` loopback pair (see ``_helpers.py``) — same code path
as production but without spinning up real WebSocket servers.
"""
from peer_discovery.membership.models import ValidationResult
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.tests._helpers import loopback_pair


def _build_pair(tmp_path):
    """Two DiscoveryNodes wired through FakeBroadcastNodes that deliver to
    each other. Returns (seed, client, seed_fake, client_fake).
    """
    seed_addr = "127.0.0.1:5678"
    client_addr = "127.0.0.1:5679"

    seed_fake, client_fake = loopback_pair(seed_addr, client_addr)

    seed_config = DiscoveryConfig(
        advertise_address=seed_addr,
        public_key_override=b"SEED-PEM",
    )
    seed = DiscoveryNode(
        "room-1", seed_config, str(tmp_path / "seed_storage"),
        broadcast_node=seed_fake,
    )
    seed_fake.pre_verify_hook = seed.lazy_register_pubkey
    seed_fake.on_message = lambda m: seed.handle_message(m)

    client_config = DiscoveryConfig(
        advertise_address=client_addr,
        public_key_override=b"CLIENT-PEM",
        bootstrap_peers=[seed_addr],
        bootstrap_timeout=2.0,
    )
    client = DiscoveryNode(
        "room-1", client_config, str(tmp_path / "client_storage"),
        broadcast_node=client_fake,
    )
    client_fake.pre_verify_hook = client.lazy_register_pubkey
    client_fake.on_message = lambda m: client.handle_message(m)

    return seed, client, seed_fake, client_fake


def test_two_nodes_bootstrap(tmp_path):
    seed, client, _, _ = _build_pair(tmp_path)

    def validator(user_id, display_name, context):
        if display_name == "Banned":
            return ValidationResult(accepted=False, reason="Banned user")
        return ValidationResult(accepted=True)
    seed.service.register_join_validator(validator)

    seed.start(display_name="Seed Node")
    try:
        client.start(display_name="Client Node")
        try:
            snap = client.service.get_membership_snapshot()
            assert seed.advertise_address in snap.members, snap.members
            assert snap.members[seed.advertise_address].display_name == "Seed Node"
            assert client.advertise_address in snap.members
            assert snap.members[client.advertise_address].display_name == "Client Node"
        finally:
            client.stop()
    finally:
        seed.stop()


def test_bootstrap_rejected(tmp_path):
    seed, client, _, _ = _build_pair(tmp_path)

    def rejector(user_id, display_name, context):
        return ValidationResult(accepted=False, reason="Go away")
    seed.service.register_join_validator(rejector)

    seed.start(display_name="Seed Node")
    try:
        # Client's start should not raise even though the seed rejects.
        client.start(display_name="Client Node")
        try:
            snap = client.service.get_membership_snapshot()
            assert len(snap.members) == 0, (
                "client snapshot should be empty after rejection, got %s"
                % list(snap.members)
            )
        finally:
            client.stop()
    finally:
        seed.stop()
