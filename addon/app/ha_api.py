import asyncio
import datetime
import json
import logging
import os
import re

import aiohttp

_LOGGER = logging.getLogger(__name__)

HA_API_URL = "http://supervisor/core/api"
_HA_WS_URL = "ws://supervisor/core/websocket"
_LOW_NOTIFICATION_ID = "battery_sentinel_low"


def _headers():
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    return {"Authorization": f"Bearer {token}"}


def _clean_name(name: str) -> str:
    """Strip redundant battery suffixes — e.g. 'Hallway Siren Battery level' -> 'Hallway Siren'.
    Only strips trailing occurrences so 'Replace battery now' is left unchanged."""
    cleaned = re.sub(r'\s+battery\s+level\s*$', '', name, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r'\s+battery\s*$', '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned or name


# ── HA data fetchers ───────────────────────────────────────────────────

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
            and s.get("state") not in ("unavailable", "unknown")
        ]

        _LOGGER.info("Found %d battery entities", len(batteries))
        return batteries

    except Exception:
        _LOGGER.exception("Failed to fetch battery entities from HA")
        return []


async def get_entity_metadata():
    """Returns dict of entity_id -> {area, device_id} for all battery entities."""
    template = (
        "{% set ns = namespace(r={}) %}"
        "{% for s in states %}"
        "{% if s.attributes.device_class == 'battery'"
        " and s.state not in ['unavailable','unknown'] %}"
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


# ── Helpers ────────────────────────────────────────────────────────────

def device_is_low(device: dict) -> bool:
    """True if the device is currently below its alert threshold."""
    threshold = device.get("alert_threshold", 15)
    if threshold == -1:
        return False
    if device["entity_id"].startswith("binary_sensor."):
        return device["state"] == "on"
    try:
        return float(device["state"]) < threshold
    except (ValueError, TypeError):
        return False


def level_str(device: dict) -> str:
    if device["entity_id"].startswith("binary_sensor."):
        return "Low" if device["state"] == "on" else "OK"
    try:
        return f"{float(device['state']):.0f}%"
    except (ValueError, TypeError):
        return f"{device['state']}%"


def _report_sort_key(device: dict) -> float:
    """Sort key: binary 'on' (low) = 0, numeric ascending, binary 'off' = 101."""
    if device["entity_id"].startswith("binary_sensor."):
        return 0 if device["state"] == "on" else 101
    try:
        return float(device["state"])
    except (ValueError, TypeError):
        return 100


def _format_line(device: dict, include_type: bool) -> str:
    area  = f" ({device['area']})" if device.get("area") else ""
    btype = f" [{device['battery_type']}]" if include_type and device.get("battery_type") else ""
    return f"- {device['name']}{area}: {level_str(device)}{btype}"


# ── Consolidated low-battery UI notification ───────────────────────────

async def update_low_battery_notification(devices: list, settings: dict):
    """Create/update or dismiss the single persistent low-battery notification."""
    if not settings.get("notify_persistent", True):
        return

    include_type = settings.get("report_include_battery_type", False)
    low = sorted([d for d in devices if device_is_low(d)], key=_report_sort_key)

    if not low:
        await _dismiss_persistent(_LOW_NOTIFICATION_ID)
        return

    _LOGGER.info("Low battery notification: %d device(s) below threshold", len(low))
    lines = [_format_line(d, include_type) for d in low]
    await _fire_persistent(
        "Battery Sentinel: Low Batteries",
        "\n".join(lines),
        notification_id=_LOW_NOTIFICATION_ID,
    )


# ── Per-device email/mobile alert (fires once on threshold crossing) ───

async def fire_low_battery_email(title: str, message: str, settings: dict, device: dict):
    """Email + mobile alert for a device that just crossed its threshold.
    Bell is intentionally excluded — that is handled by the consolidated notification."""
    service = settings.get("notify_email_service", "").strip()
    if device.get("notify_email", True) and service:
        addr = device.get("notify_email_address", "") or settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            await _fire_notify_service(service, title, message, targets)

    if device.get("notify_mobile", False):
        mobile = (device.get("notify_mobile_service", "").strip()
                  or settings.get("notify_mobile_default_service", "").strip())
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


# ── General notification (bell + email — new device alerts, etc.) ──────

async def fire_notification(title: str, message: str, settings: dict, device: dict = None, use_bell: bool = True):
    """Fire bell and/or email. Used for new-device alerts and daily report."""
    use_bell  = use_bell and (device.get("notify_bell",  True) if device else settings.get("notify_persistent", True))
    use_email = device.get("notify_email", True) if device else True

    if use_bell:
        await _fire_persistent(title, message)

    service = settings.get("notify_email_service", "").strip()
    if use_email and service:
        addr = (device.get("notify_email_address", "") if device else "") \
               or settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            await _fire_notify_service(service, title, message, targets)

    if device:
        mobile = device.get("notify_mobile_service", "").strip()
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


# ── Daily report ───────────────────────────────────────────────────────

_ICON_URL = "https://raw.githubusercontent.com/smcneece/battery-sentinel/main/addon/icon.png"
_REPO_URL = "https://github.com/smcneece/battery-sentinel"


def _level_color(device: dict) -> str:
    if device["entity_id"].startswith("binary_sensor."):
        return "#cc3333" if device["state"] == "on" else "#4c4"
    try:
        pct = float(device["state"])
        if pct < 10:  return "#cc3333"
        if pct < 25:  return "#cc8800"
        return "#4c4"
    except (ValueError, TypeError):
        return "#888"


def _build_report_html(low: list, ok: list, settings: dict, now: datetime.datetime, include_all: bool) -> str:
    include_type = settings.get("report_include_battery_type", False)
    timestamp = now.strftime("%B %d, %Y at %I:%M %p")
    cols = 4 if include_type else 3

    def device_row(d, stripe):
        bg    = "#fff9f9" if stripe and device_is_low(d) else ("#f9f9f9" if stripe else "#fff")
        color = _level_color(d)
        lvl   = level_str(d)
        area  = d.get("area") or ""
        btype = f"<td style='padding:7px 14px;color:#888;font-size:.85em'>{d.get('battery_type','')}</td>" if include_type else ""
        return (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:7px 14px;color:#222'>{d['name']}</td>"
            f"<td style='padding:7px 14px;color:#666;font-size:.9em'>{area}</td>"
            f"<td style='padding:7px 14px;font-weight:bold;text-align:right;color:{color}'>{lvl}</td>"
            f"{btype}</tr>"
        )

    def section(heading, accent, devices):
        if not devices:
            return ""
        type_th = f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Type</th>" if include_type else ""
        hdr = (
            f"<tr><td colspan='{cols}' style='padding:16px 14px 6px;font-weight:bold;"
            f"color:{accent};border-bottom:2px solid {accent};font-size:.92em'>"
            f"{heading} <span style='font-weight:normal;color:#aaa'>({len(devices)})</span></td></tr>"
            f"<tr style='background:#f8f8f8'>"
            f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Device</th>"
            f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Room</th>"
            f"<th style='padding:7px 14px;text-align:right;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Level</th>"
            f"{type_th}</tr>"
        )
        rows = "".join(device_row(d, i % 2 == 0) for i, d in enumerate(devices))
        spacer = f"<tr><td colspan='{cols}' style='padding:8px'></td></tr>"
        return hdr + rows + spacer

    if not low and not ok:
        body = (
            f"<tr><td colspan='{cols}' style='padding:32px;text-align:center;color:#4c4;font-size:1.05em'>"
            f"&#10003; All batteries are OK &mdash; nothing to report."
            f"</td></tr>"
        )
    elif include_all:
        body = section("&#9888; Needs Attention", "#cc3333", low) + section("&#10003; All Batteries", "#555", ok)
    else:
        body = section("&#9888; Low Batteries", "#cc3333", low)

    return (
        f"<!DOCTYPE html><html><body style='margin:0;padding:20px;background:#efefef;"
        f"font-family:Arial,Helvetica,sans-serif'>"
        f"<table width='100%' cellpadding='0' cellspacing='0' style='max-width:640px;margin:0 auto;"
        f"border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.15)'>"
        f"<tr><td style='background:#1a1a2e;padding:18px 20px'>"
        f"<img src='{_ICON_URL}' width='30' height='30' style='vertical-align:middle;border-radius:4px' alt=''>"
        f"<span style='color:#fff;font-size:1.1em;font-weight:bold;vertical-align:middle;margin-left:8px'>Battery Sentinel</span>"
        f"<div style='color:#888;font-size:.8em;margin-top:5px;padding-left:38px'>Daily Battery Report &mdash; {timestamp}</div>"
        f"</td></tr>"
        f"<tr><td style='background:#fff;padding:4px 0'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'>{body}</table>"
        f"</td></tr>"
        f"<tr><td style='background:#f5f5f5;padding:12px 20px;text-align:center;"
        f"border-top:1px solid #e0e0e0'>"
        f"<span style='color:#aaa;font-size:.78em'>"
        f"<a href='{_REPO_URL}' style='color:#58a6ff;text-decoration:none'>Battery Sentinel</a>"
        f" &mdash; Home Assistant Battery Monitor</span>"
        f"</td></tr></table></body></html>"
    )


async def send_daily_report(devices: list, settings: dict):
    """Build and send the daily battery report email."""
    include_all  = settings.get("daily_report_include_all", False)
    include_type = settings.get("report_include_battery_type", False)
    now = datetime.datetime.now()

    if include_all:
        low = sorted([d for d in devices if device_is_low(d)], key=_report_sort_key)
        ok  = sorted([d for d in devices if not device_is_low(d) and d.get("alert_threshold", 15) != -1], key=_report_sort_key)
        all_devices = low + ok
    else:
        low = sorted([d for d in devices if device_is_low(d)], key=_report_sort_key)
        ok  = []
        all_devices = low

    if not all_devices:
        if settings.get("daily_report_send_if_ok"):
            _LOGGER.info("Daily report: all batteries OK, sending all-clear")
        else:
            _LOGGER.info("Daily report: nothing to report, skipping send")
            return

    service = settings.get("notify_email_service", "").strip()
    if not service:
        return

    addr = settings.get("notify_email_to", "")
    targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
    cc = settings.get("notify_email_cc", "")
    if cc:
        targets.extend(a.strip() for a in cc.split(",") if a.strip())
    if not targets:
        return

    html = _build_report_html(low, ok, settings, now, include_all)

    await _fire_notify_service(service, "Battery Sentinel: Daily Battery Report", html, targets, html=html)
    _LOGGER.info("Daily report sent (%d device(s))", len(all_devices))


# ── Script trigger ────────────────────────────────────────────────────

async def fire_script(script_entity_id: str, device: dict):
    """Fire a HA script with battery device context passed as variables."""
    variables = {
        "device_name":   device.get("name", ""),
        "battery_level": level_str(device),
        "battery_type":  device.get("battery_type", ""),
        "area":          device.get("area", ""),
        "entity_id":     device["entity_id"],
    }
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{HA_API_URL}/services/script/turn_on",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"entity_id": script_entity_id, "variables": variables},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        _LOGGER.info("Script '%s' fired for %s", script_entity_id, device["entity_id"])
    except Exception:
        _LOGGER.exception("Failed to fire script '%s' for %s", script_entity_id, device["entity_id"])


# ── Entity registry ───────────────────────────────────────────────────

async def rename_entity(entity_id: str, new_name: str) -> bool:
    """Rename an entity's friendly name via the HA WebSocket API."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
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


# ── Primitives ─────────────────────────────────────────────────────────

async def _fire_persistent(title: str, message: str, notification_id: str = None):
    payload = {"title": title, "message": message}
    if notification_id:
        payload["notification_id"] = notification_id
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{HA_API_URL}/services/persistent_notification/create",
                headers={**_headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        _LOGGER.info("Persistent notification fired: %s", title)
    except Exception:
        _LOGGER.exception("Failed to fire persistent notification")


async def _dismiss_persistent(notification_id: str):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{HA_API_URL}/services/persistent_notification/dismiss",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"notification_id": notification_id},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        _LOGGER.info("Persistent notification dismissed: %s", notification_id)
    except Exception:
        _LOGGER.exception("Failed to dismiss persistent notification %s", notification_id)


async def _fire_notify_service(service: str, title: str, message: str, targets: list, html: str = None):
    payload = {"title": title, "message": message}
    if targets:
        payload["target"] = targets
    if not service.startswith("mobile_app_"):
        if html:
            payload["data"] = {"html": html}
        else:
            html_body = "<br>".join(message.split("\n"))
            payload["data"] = {"html": f"<html><body>{html_body}</body></html>"}
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{HA_API_URL}/services/notify/{service}",
                headers={**_headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        _LOGGER.info("Notify service '%s' fired: %s", service, title)
    except Exception:
        _LOGGER.exception("Failed to fire notify service '%s'", service)
