"""Home Assistant connection settings.

Supports both Home Assistant Supervisor add-on mode and standalone Docker mode.
- Add-on mode (default): SUPERVISOR_TOKEN + supervisor proxy URLs
- Docker mode: HA_BASE_URL + HA_TOKEN
"""

import os

def _base_url() -> str:
    return os.environ.get("HA_BASE_URL", "http://supervisor/core").rstrip("/")


def _api_base() -> str:
    base = _base_url()
    return f"{base}/api" if not base.endswith("/api") else base


def _ws_url() -> str:
    base = _base_url()()
    if base.startswith("https://"):
        return "wss://" + base[len("https://"):] + "/websocket"
    if base.startswith("http://"):
        return "ws://" + base[len("http://"):] + "/websocket"
    return base + "/websocket"


HA_API_URL = _api_base()
_HA_WS_URL = _ws_url()

def _access_token() -> str:
    """Return HA access token from environment.

    HA_TOKEN is preferred for standalone Docker use.
    SUPERVISOR_TOKEN is used automatically in HA add-on environments.
    """
    return os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN", "")


def _headers():
    """Return auth header.

    HA_TOKEN is preferred for standalone Docker use.
    SUPERVISOR_TOKEN is used automatically in HA add-on environments.
    """
    return {"Authorization": f"Bearer {_access_token()}"}
