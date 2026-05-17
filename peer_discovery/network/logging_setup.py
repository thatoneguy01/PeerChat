"""Logging helpers for the peer_discovery package.

Python defaults all loggers to WARNING unless configured. That means INFO-level
diagnostics in peer_discovery don't show up in the console by default — which
hides exactly the information you need to diagnose join / gossip / bootstrap
issues during a live demo.

Call ``configure_discovery_logging()`` once at process start (e.g., in
``main.py`` before ``app.run``) to make peer_discovery's INFO-level lines
visible. Keep DEBUG off unless you really want the per-heartbeat noise.

Example:

    from peer_discovery.network.logging_setup import configure_discovery_logging
    configure_discovery_logging()  # INFO level
    # configure_discovery_logging(level=logging.DEBUG)  # very verbose

This function is idempotent — calling it twice is fine.
"""
from __future__ import annotations

import logging
import sys


_CONFIGURED = False


def configure_discovery_logging(
    level: int = logging.INFO,
    *,
    stream=None,
    add_handler_if_root_empty: bool = True,
) -> None:
    """Set log level for all ``peer_discovery.*`` loggers.

    Parameters
    ----------
    level
        Log level for peer_discovery loggers. Default: ``logging.INFO``.
    stream
        If provided, attach a ``StreamHandler`` writing to this stream to the
        peer_discovery logger. Otherwise relies on the root logger's handlers
        (or installs one on the root if the root has none — see below).
    add_handler_if_root_empty
        If True and the root logger has no handlers, install a basic
        ``StreamHandler`` on the root so emitted records actually print. This
        matches the Flask dev-server default which otherwise silently drops
        any record that isn't handled by Flask's own logger.
    """
    global _CONFIGURED

    pd_logger = logging.getLogger("peer_discovery")
    pd_logger.setLevel(level)
    # Propagate up to root so existing handlers (e.g., Flask's) pick records up.
    pd_logger.propagate = True

    if stream is not None:
        handler = logging.StreamHandler(stream)
        handler.setLevel(level)
        handler.setFormatter(_default_formatter())
        pd_logger.addHandler(handler)
    elif add_handler_if_root_empty:
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setLevel(level)
            handler.setFormatter(_default_formatter())
            root.addHandler(handler)
            root.setLevel(level)

    _CONFIGURED = True


def _default_formatter() -> logging.Formatter:
    """Compact one-line format: time, level, logger short name, message."""
    return logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
