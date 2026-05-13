from peer_discovery.membership.duplicate_guard import DuplicateGuard


def test_first_then_duplicate():
    g = DuplicateGuard(window_size=10)
    assert g.is_duplicate("alice", "JOIN_REQUESTED") is False
    assert g.is_duplicate("alice", "JOIN_REQUESTED") is True


def test_different_keys_independent():
    g = DuplicateGuard(window_size=10)
    assert g.is_duplicate("alice", "JOIN_REQUESTED") is False
    assert g.is_duplicate("bob", "JOIN_REQUESTED") is False
    assert g.is_duplicate("alice", "LEAVE_REQUESTED") is False


def test_eviction_after_window_overflow():
    g = DuplicateGuard(window_size=3)
    g.is_duplicate("a", "X")
    g.is_duplicate("b", "X")
    g.is_duplicate("c", "X")
    # Inserting one more triggers eviction of the oldest ("a", "X")
    g.is_duplicate("d", "X")
    assert g.is_duplicate("a", "X") is False  # evicted, treated fresh


def test_clear_resets():
    g = DuplicateGuard(window_size=10)
    g.is_duplicate("alice", "JOIN_REQUESTED")
    g.clear()
    assert g.is_duplicate("alice", "JOIN_REQUESTED") is False
