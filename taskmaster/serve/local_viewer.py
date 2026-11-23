# User intent: the local server's own guarded viewer on the project's hashed 6800-6899 port, opened for the person on this machine
# with a one-time sign-in link instead of a token prompt (local-server-004); a taken port never stops the server.
"""Band walk for the local profile's viewer (spec section 2, plan section 0 "Viewer bind")."""
from __future__ import annotations

import logging

from .config import ServeUnavailable
from .viewer import start_viewer

LOG = logging.getLogger("taskmaster.serve")


def start_band_viewer(bs, guard):
    """Bind `guard`'s viewer on the first free port of the project's band, walking on from its hashed port.

    A fallback session's solo viewer (or another program) may hold a port: the identity check only decides what the log
    says, because either way the guarded viewer moves on. Returns (server, thread, port), or None when no port in the band
    is free (the server then runs without a viewer; it is never fatal)."""
    band = bs._PORT_BAND
    target = bs._project_port()
    width = len(band)
    for step in range(width):
        port = band.start + (target - band.start + step) % width
        if port in bs.BROWSER_BLOCKED_PORTS:
            continue
        try:
            return start_viewer(bs, guard, port)
        except ServeUnavailable:
            holder = "this project's solo viewer" if bs._check_identity(port) else "another program"
            LOG.info("viewer port %s is taken by %s; trying the next", port, holder)
    LOG.warning("no port in %s-%s is free: the local server runs without a viewer", band.start, band.stop - 1)
    return None
