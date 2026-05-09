"""HA connection constants and auth header factory.
Supports two modes:
  Supervisor -- default; runs as an HAOS add-on via http://supervisor/core
  Docker     -- set HA_BASE_URL (e.g. http://192.168.1.64:8123) and HA_TOKEN
                to connect to any HA instance from a plain Docker container."""

import os

_BASE_URL = os.environ.get("HA_BASE_URL", "").rstrip("/")

if _BASE_URL:
    HA_API_URL = f"{_BASE_URL}/api"
    _HA_WS_URL = (
        _BASE_URL.replace("https://", "wss://").replace("http://", "ws://")
        + "/api/websocket"
    )
else:
    HA_API_URL = "http://supervisor/core/api"
    _HA_WS_URL = "ws://supervisor/core/websocket"


def _token() -> str:
    """Return the auth token for the current mode."""
    if _BASE_URL:
        return os.environ.get("HA_TOKEN", "")
    return os.environ.get("SUPERVISOR_TOKEN", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}
