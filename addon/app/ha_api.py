"""HA data fetchers -- all communication with the Supervisor API and WebSocket.
This module is intentionally limited to reading data from HA; no notification
logic lives here (see notifications.py)."""

import asyncio
import json
import logging
import os
import re

import aiohttp

from ha_config import HA_API_URL, _HA_WS_URL, _access_token, _headers

_LOGGER = logging.getLogger(__name__)


def _clean_name(name: str) -> str:
    """Strip redundant battery suffixes — e.g. 'Hallway Siren Battery level' -> 'Hallway Siren'.
    Only strips trailing occurrences so 'Replace battery now' is left unchanged."""
    cleaned = re.sub(r'\s+battery\s+level\s*$', '', name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\s+battery\s*$', '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned or name


# ── HA data fetchers ───────────────────────────────────────────────────

async def get_ha_timezone() -> str:
    """Fetch the HA configured timezone string (e.g. 'America/Denver')."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{HA_API_URL}/config",
                headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                return data.get("time_zone", "")
    except Exception:
        _LOGGER.exception("Failed to fetch HA config for timezone")
        return ""


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
    token = _access_token()
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
    token = _access_token()
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
    token = _access_token()
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


