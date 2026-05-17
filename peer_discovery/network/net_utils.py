"""Network utility helpers for the discovery layer."""
import socket


def pick_free_port(start: int = 8001, end: int = 8020) -> int:
    """Find the first free TCP port in [start, end] on this host.

    Used by callers that don't care which port they bind, as long as it's
    consistent for the lifetime of the process. Two nodes on the same machine
    will end up on different ports (8001 for the first, 8002 for the second,
    etc.) without any coordination.

    Raises OSError if every port in the range is taken.
    """
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
            except OSError:
                continue
            return port
    raise OSError(f"No free port in [{start}, {end}]")


def get_lan_ip() -> str:
    """Return this machine's primary LAN IP.

    Uses the standard UDP-socket trick: opens a UDP socket to a public address
    (no packet is actually sent) and reads back the local endpoint the OS
    selected. Works on any machine that has a default route, including when
    behind NAT.

    Falls back to 127.0.0.1 if no route is available (e.g. fully offline).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()
