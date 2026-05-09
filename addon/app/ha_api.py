"""HA data fetchers -- all communication with the Supervisor API and WebSocket.
This module is intentionally limited to reading data from HA; no notification
logic lives here (see notifications.py)."""

import asyncio
import json
import logging
import os
import re

import aiohttp

from ha_config import HA_API_URL, _HA_WS_URL, _headers, _token

_LOGGER = logging.getLogger(__name__)


def _clean_name(name: str) -> str:
    """Strip redundant battery suffixes — e.g. 'Hallway Siren Battery level' -> 'Hallway Siren'.
    Only strips trailing occurrences so 'Replace battery now' is left unchanged."""
    cleaned = re.sub(r'\s+battery\s+level\s*$', '', name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\s+battery\s*$', '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned or name


# ── HA data fetchers ───────────────────────────────────────────────────

async def get_ha_config() -> dict:
    """Fetch HA /api/config and return the parsed JSON, or {} on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/config",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {}
                return await resp.json()
    except Exception:
        _LOGGER.exception("Failed to fetch HA config")
        return {}


async def get_ha_timezone() -> str:
    """Fetch the HA configured timezone string (e.g. 'America/Denver')."""
    data = await get_ha_config()
    return data.get("time_zone", "")


async def get_ha_version() -> str:
    """Fetch the running HA version string (e.g. '2025.5.3')."""
    data = await get_ha_config()
    return data.get("version", "")


async def get_battery_entities():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/states",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.error("HA API returned %s", resp.status)
                    return []
                states = await resp.json()

        batteries = [
            {
                "entity_id": s["entity_id"],
                "state": s["state"],
                "name": _clean_name(s["attributes"].get("friendly_name", s["entity_id"])),
                "unit": s["attributes"].get("unit_of_measurement", "%"),
            }
            for s in states
            if s.get("attributes", {}).get("device_class") == "battery"
        ]

        _LOGGER.info("Found %d battery entities", len(batteries))
        return batteries

    except Exception:
        _LOGGER.exception("Failed to fetch battery entities from HA")
        return []


async def get_hidden_entity_ids() -> set:
    """Returns entity_ids marked not-visible in the HA entity registry.
    Uses WebSocket because the REST entity registry endpoint is not exposed through the Supervisor proxy."""
    token = _token()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                _HA_WS_URL, timeout=aiohttp.ClientTimeout(total=15)
            ) as ws:
                msg = await ws.receive_json()
                if msg.get("type") != "auth_required":
                    return set()
                await ws.send_json({"type": "auth", "access_token": token})
                msg = await ws.receive_json()
                if msg.get("type") != "auth_ok":
                    _LOGGER.warning("WebSocket auth failed for entity registry fetch")
                    return set()
                await ws.send_json({"id": 1, "type": "config/entity_registry/list"})
                msg = await ws.receive_json()
                entries = msg.get("result", []) or []
        hidden = {e["entity_id"] for e in entries if e.get("hidden_by")}
        _LOGGER.debug("Entity registry: %d hidden entities", len(hidden))
        return hidden
    except Exception:
        _LOGGER.exception("Failed to fetch entity registry via WebSocket")
        return set()


async def get_entity_metadata():
    """Returns dict of entity_id -> {area, device_id} for all battery entities."""
    template = (
        "{% set ns = namespace(r={}) %}"
        "{% for s in states %}"
        "{% if s.attributes.device_class == 'battery' %}"
        "{% set ns.r = dict(ns.r, **{s.entity_id: {"
        "'area': area_name(s.entity_id) or '',"
        "'device_id': device_id(s.entity_id) or ''"
        "}}) %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.r | tojson }}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HA_API_URL}/template",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"template": template},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Template API returned %s for metadata fetch", resp.status)
                    return {}
                return json.loads(await resp.text())
    except Exception:
        _LOGGER.exception("Failed to fetch entity metadata")
        return {}


async def get_zigbee_last_seen_entities() -> list:
    """Fetch sensor.*_last_seen entities created by Zigbee2MQTT (platform=mqtt only).
    Returns list of {entity_id, name, state}."""
    _NAME_CLEANUP = re.compile(r'\s+last\s+seen\s*$', re.IGNORECASE)
    token = _token()
    try:
        # Get MQTT entity IDs from registry to exclude Z-Wave and other platforms
        mqtt_entity_ids: set = set()
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                _HA_WS_URL, timeout=aiohttp.ClientTimeout(total=15)
            ) as ws:
                msg = await ws.receive_json()
                if msg.get("type") == "auth_required":
                    await ws.send_json({"type": "auth", "access_token": token})
                    msg = await ws.receive_json()
                    if msg.get("type") == "auth_ok":
                        await ws.send_json({"id": 1, "type": "config/entity_registry/list"})
                        msg = await ws.receive_json()
                        mqtt_entity_ids = {
                            e["entity_id"]
                            for e in (msg.get("result") or [])
                            if e.get("platform") == "mqtt"
                        }

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/states",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                states = await resp.json()

        return [
            {
                "entity_id": s["entity_id"],
                "name": _NAME_CLEANUP.sub(
                    "", s["attributes"].get("friendly_name", s["entity_id"])
                ).strip(),
                "state": s["state"],
            }
            for s in states
            if (
                s["entity_id"].startswith("sensor.")
                and s["entity_id"].endswith("_last_seen")
                and s.get("attributes", {}).get("device_class") == "timestamp"
                and (not mqtt_entity_ids or s["entity_id"] in mqtt_entity_ids)
            )
        ]
    except Exception:
        _LOGGER.exception("Failed to fetch Zigbee last_seen entities")
        return []


async def get_monitored_entity_device_ids() -> set:
    """Returns device_ids for all sensor.*_node_status and sensor.*_last_seen entities.
    Used to suppress duplicate unavailable alerts for devices tracked by Z-Wave/Zigbee monitors."""
    template = (
        "{% set ns = namespace(r={}) %}"
        "{% for s in states.sensor %}"
        "{% if s.entity_id.endswith('_node_status') or s.entity_id.endswith('_last_seen') %}"
        "{% set did = device_id(s.entity_id) or '' %}"
        "{% if did %}{% set ns.r = dict(ns.r, **{s.entity_id: did}) %}{% endif %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.r | tojson }}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HA_API_URL}/template",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"template": template},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return set()
                return set(json.loads(await resp.text()).values())
    except Exception:
        _LOGGER.exception("Failed to fetch monitored entity device IDs")
        return set()


async def get_zigbee_node_areas() -> dict:
    """Returns dict of entity_id -> area string for all sensor.*_last_seen entities."""
    template = (
        "{% set ns = namespace(r={}) %}"
        "{% for s in states.sensor %}"
        "{% if s.entity_id.endswith('_last_seen') %}"
        "{% set ns.r = dict(ns.r, **{s.entity_id: area_name(s.entity_id) or ''}) %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.r | tojson }}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HA_API_URL}/template",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"template": template},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {}
                return json.loads(await resp.text())
    except Exception:
        _LOGGER.exception("Failed to fetch Zigbee node areas")
        return {}


async def get_zwave_node_areas() -> dict:
    """Returns dict of entity_id -> area string for all sensor.*_node_status entities.
    Uses the HA template API since node status sensors aren't battery entities and
    are excluded from get_entity_metadata()."""
    template = (
        "{% set ns = namespace(r={}) %}"
        "{% for s in states.sensor %}"
        "{% if s.entity_id.endswith('_node_status') %}"
        "{% set ns.r = dict(ns.r, **{s.entity_id: area_name(s.entity_id) or ''}) %}"
        "{% endif %}"
        "{% endfor %}"
        "{{ ns.r | tojson }}"
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HA_API_URL}/template",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"template": template},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {}
                return json.loads(await resp.text())
    except Exception:
        _LOGGER.exception("Failed to fetch Z-Wave node areas")
        return {}


async def get_scripts() -> list:
    """Returns sorted list of {entity_id, name} for all HA scripts."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/states",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                states = await resp.json()
        return sorted(
            [
                {
                    "entity_id": s["entity_id"],
                    "name": s["attributes"].get("friendly_name", s["entity_id"]),
                }
                for s in states
                if s["entity_id"].startswith("script.")
            ],
            key=lambda x: x["name"].lower(),
        )
    except Exception:
        _LOGGER.exception("Failed to fetch scripts")
        return []


async def get_notify_services() -> list:
    """Returns sorted list of service slugs under the HA notify domain."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/services",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                all_services = await resp.json()
        for domain_obj in all_services:
            if domain_obj.get("domain") == "notify":
                return sorted(domain_obj.get("services", {}).keys())
        return []
    except Exception:
        _LOGGER.exception("Failed to fetch notify services")
        return []


# ── Battery Notes lookup ───────────────────────────────────────────────

# ── Battery Notes community database ──────────────────────────────────
# library.json is the bundled snapshot of https://github.com/andrew-codechimp/HA-Battery-Notes
_BATTERY_NOTES_PATH = os.path.join(os.path.dirname(__file__), "library.json")


async def get_device_registry() -> dict:
    """Returns dict of device_id -> {manufacturer, model} for all HA devices."""
    token = _token()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(_HA_WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if msg.get("type") != "auth_required":
                    return {}
                await ws.send_json({"type": "auth", "access_token": token})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if msg.get("type") != "auth_ok":
                    return {}
                await ws.send_json({"id": 1, "type": "config/device_registry/list"})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=10)
                if not msg.get("success"):
                    return {}
                return {
                    dev["id"]: {
                        "manufacturer": dev.get("manufacturer") or "",
                        "model":        dev.get("model") or "",
                    }
                    for dev in msg.get("result", [])
                    if dev.get("id")
                }
    except asyncio.TimeoutError:
        _LOGGER.error("Timeout fetching device registry")
        return {}
    except Exception:
        _LOGGER.exception("Failed to fetch device registry")
        return {}


def fetch_battery_notes_db() -> list:
    """Load the bundled Battery Notes community database JSON."""
    try:
        with open(_BATTERY_NOTES_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            return raw.get("devices", raw.get("data", []))
        if isinstance(raw, list):
            return raw
        return []
    except Exception:
        _LOGGER.exception("Failed to load bundled Battery Notes database")
        return []


def _normalize_type(s: str) -> str:
    return re.sub(r'[\s\-]', '', s).upper()


def lookup_battery_types(devices: list, registry: dict, db: list) -> tuple:
    """Match devices against the Battery Notes DB by manufacturer+model.
    Returns (auto_fill, conflicts):
      auto_fill -- device has no type set and the DB has a match; safe to apply automatically
      conflicts -- device already has a type but it differs from the DB suggestion; user decides
    """
    _SKIP_TYPES = {"MANUAL"}

    db_lookup: dict = {}
    for entry in db:
        mfr   = (entry.get("manufacturer") or "").strip().lower()
        mdl   = (entry.get("model")        or "").strip().lower()
        btype = (entry.get("battery_type") or "").strip()
        if mfr and mdl and btype and btype.upper() not in _SKIP_TYPES:
            db_lookup[(mfr, mdl)] = btype

    auto_fill: list = []
    conflicts: list = []
    for device in devices:
        did = device.get("device_id", "")
        if not did:
            continue
        reg = registry.get(did, {})
        mfr = (reg.get("manufacturer") or "").strip().lower()
        mdl = (reg.get("model")        or "").strip().lower()
        if not mfr or not mdl:
            continue
        suggested = db_lookup.get((mfr, mdl))
        if not suggested:
            continue
        current = device.get("battery_type", "")
        row = {
            "entity_id":      device["entity_id"],
            "name":           device["name"],
            "current_type":   current,
            "suggested_type": suggested,
        }
        if not current:
            auto_fill.append(row)
        elif _normalize_type(current) != _normalize_type(suggested):
            conflicts.append(row)

    return auto_fill, conflicts


# ── Entity registry ───────────────────────────────────────────────────

async def rename_entity(entity_id: str, new_name: str) -> bool:
    """Rename an entity's friendly name via the HA WebSocket API."""
    token = _token()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(_HA_WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                _LOGGER.info("WS rename: step1 type=%s", msg.get("type"))
                if msg.get("type") != "auth_required":
                    _LOGGER.warning("WS rename: expected auth_required, got %s", msg)
                    return False
                await ws.send_json({"type": "auth", "access_token": token})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                _LOGGER.info("WS rename: step2 type=%s", msg.get("type"))
                if msg.get("type") != "auth_ok":
                    _LOGGER.warning("WS rename: auth failed: %s", msg)
                    return False
                await ws.send_json({
                    "id": 1,
                    "type": "config/entity_registry/update",
                    "entity_id": entity_id,
                    "name": new_name or None,
                })
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                _LOGGER.info("WS rename: result = %s", msg)
                if not msg.get("success"):
                    _LOGGER.warning("WS rename: update failed: %s", msg)
                    return False
                return True
    except asyncio.TimeoutError:
        _LOGGER.error("WS rename timed out for %s", entity_id)
        return False
    except Exception:
        _LOGGER.exception("Failed to rename entity %s via WebSocket", entity_id)
        return False


async def get_entity_history(entity_id: str, start_str: str, end_str: str) -> list:
    """Fetch raw recorder history for entity_id between start_str and end_str (UTC Z strings).
    Returns [{t: unix_ms, v: float}]. Binary sensor on/off mapped to 0/100."""
    import datetime
    from urllib.parse import urlencode
    params = urlencode({
        "filter_entity_id": entity_id,
        "end_time": end_str,
        "minimal_response": "true",
        "no_attributes": "true",
    })
    url = f"{HA_API_URL}/history/period/{start_str}?{params}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, headers=_headers(), timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("History API returned %d for %s", resp.status, entity_id)
                    return []
                data = await resp.json()
        _LOGGER.debug("History for %s: %d series, %d points",
                      entity_id, len(data), len(data[0]) if data else 0)
        if not data or not data[0]:
            return []
        points = []
        for item in data[0]:
            state = item.get("state", "")
            ts    = item.get("last_changed", "")
            if state in ("unavailable", "unknown", ""):
                continue
            if state == "on":
                v = 0.0
            elif state == "off":
                v = 100.0
            else:
                try:
                    v = float(state)
                except (ValueError, TypeError):
                    continue
            try:
                dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                t_ms = int(dt.timestamp() * 1000)
            except Exception:
                continue
            points.append({"t": t_ms, "v": v})
        return points
    except Exception:
        _LOGGER.exception("Failed to fetch history for %s", entity_id)
        return []


async def get_entity_statistics(entity_id: str, start_str: str, end_str: str, period: str = "day") -> list:
    """Fetch long-term statistics via WebSocket (recorder/statistics_during_period).
    Returns [{t: unix_ms, v: float}] using mean values. Falls back gracefully."""
    import datetime
    token = _token()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(_HA_WS_URL) as ws:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if msg.get("type") != "auth_required":
                    return []
                await ws.send_json({"type": "auth", "access_token": token})
                msg = await asyncio.wait_for(ws.receive_json(), timeout=5)
                if msg.get("type") != "auth_ok":
                    return []
                await ws.send_json({
                    "id": 1,
                    "type": "recorder/statistics_during_period",
                    "start_time":    start_str.replace("Z", "+00:00"),
                    "end_time":      end_str.replace("Z", "+00:00"),
                    "statistic_ids": [entity_id],
                    "period":        period,
                })
                msg = await asyncio.wait_for(ws.receive_json(), timeout=30)
                if not msg.get("success"):
                    _LOGGER.warning("Statistics WS failed for %s: %s", entity_id, msg.get("error"))
                    return []
                data = msg.get("result", {})
        stats = data.get(entity_id, [])
        points = []
        for item in stats:
            v_raw = item.get("mean")
            if v_raw is None:
                v_raw = item.get("state")  # fallback for non-measurement state_class
            if v_raw is None:
                continue
            ts = item.get("start", "")
            try:
                t_ms = int(ts) if isinstance(ts, (int, float)) else int(datetime.datetime.fromisoformat(ts).timestamp() * 1000)
            except Exception:
                continue
            points.append({"t": t_ms, "v": round(float(v_raw), 1)})
        _LOGGER.debug("Statistics for %s (%s): %d points", entity_id, period, len(points))
        return points
    except Exception:
        _LOGGER.exception("Failed to fetch statistics for %s", entity_id)
        return []


