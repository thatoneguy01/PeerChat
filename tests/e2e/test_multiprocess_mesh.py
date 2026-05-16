"""
Multi-process E2E tests (SDET-style).

Each test starts real peer_worker OS processes (like mini containers on one host),
syncs RSA pubkeys, sends hybrid-encrypted signed chat, and asserts all inboxes.
"""

from __future__ import annotations

import pytest

from e2e.mesh import PeerMesh


@pytest.mark.e2e
def test_encrypted_message_delivered_to_all_peers(local_mesh: PeerMesh):
    text = "e2e-multiprocess-hybrid-hello"
    local_mesh.clear_all_inboxes()

    sent = local_mesh.send_from(0, text)
    assert sent.get("ok") is True

    results = local_mesh.wait_for_plaintext(text, expected_count=3, timeout=15.0)
    assert len(results) == 3
    local_mesh.assert_encrypted_on_wire(text)


@pytest.mark.e2e
def test_second_message_after_roster_already_synced(local_mesh: PeerMesh):
    local_mesh.clear_all_inboxes()

    first = "first-e2e-msg"
    second = "second-e2e-msg"

    local_mesh.send_from(0, first)
    local_mesh.wait_for_plaintext(first, expected_count=3, timeout=15.0)

    local_mesh.clear_all_inboxes()
    local_mesh.send_from(1, second)
    results = local_mesh.wait_for_plaintext(second, expected_count=3, timeout=15.0)
    assert len(results) == 3


@pytest.mark.e2e
def test_empty_inbox_before_send(local_mesh: PeerMesh):
    local_mesh.clear_all_inboxes()
    assert local_mesh._collect_matching("anything") == {}
