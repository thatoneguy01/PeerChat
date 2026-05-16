"""Fixtures for multi-process E2E tests."""

from __future__ import annotations

import pytest

from e2e.mesh import PeerMesh


@pytest.fixture
def local_mesh():
    """
    Three peer_worker subprocesses on distinct ports.

    Ports 5301–5303 (WS) and 15301–15303 (control) avoid clashing with demo defaults.
    """
    mesh = PeerMesh(
        peer_count=3,
        base_ws_port=5301,
        base_control_port=15301,
    )
    mesh.start()
    yield mesh
    mesh.stop()
