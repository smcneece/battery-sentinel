"""HA Supervisor connection constants and auth header factory.
Centralised here so every module imports from one place instead of duplicating strings."""

import os

# Supervisor proxy endpoints -- only reachable from inside an HA add-on container
HA_API_URL = "http://supervisor/core/api"
_HA_WS_URL = "ws://supervisor/core/websocket"


def _headers():
    """Return auth header using the Supervisor-injected token."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}
