"""All outbound notification logic -- HA persistent notifications, email, mobile, and script triggers.

Notification strategy:
  - Per-device low alert: fires once when a device transitions to low (alert_sent flag in storage).
    Resets when the device recovers above threshold.
  - Persistent bell notification: updated every scan to reflect current low devices.
  - Unavailable alert: fires once after a configurable delay (unavailable_sent flag in storage).
  - Daily report: fires once per day at the configured time (last_report_date in storage).
"""

import datetime
import logging

import aiohttp

from ha_config import HA_API_URL, _HA_WS_URL, _headers
from device_utils import device_is_low, level_str, report_sort_key, format_line
from email_html import build_unavailable_html, build_recovery_html, build_report_html

_LOGGER = logging.getLogger(__name__)

# Stable notification ID so HA updates the same bell entry rather than stacking new ones
_LOW_NOTIFICATION_ID = "battery_sentinel_low"


def is_muted_now(device: dict, now: datetime.datetime) -> bool:
    mu = device.get("muted_until")
    if not mu:
        return False
    try:
        mu_dt = datetime.datetime.fromisoformat(mu)
        # Frontend saves UTC ISO strings (with Z offset); compare like-for-like
        if mu_dt.tzinfo is not None:
            return datetime.datetime.now(datetime.timezone.utc) < mu_dt
        return now < mu_dt
    except Exception:
        return False


# ── Consolidated low-battery UI notification ───────────────────────────

async def update_low_battery_notification(devices: list, settings: dict):
    if not settings.get("notify_persistent", True):
        return

    include_type = settings.get("report_include_battery_type", False)
    now = datetime.datetime.now()
    low = sorted(
        [d for d in devices if device_is_low(d) and d.get("notify_bell", True) and not is_muted_now(d, now)],
        key=report_sort_key,
    )

    if not low:
        await _dismiss_persistent(_LOW_NOTIFICATION_ID)
        return

    _LOGGER.info("Low battery notification: %d device(s) below threshold", len(low))
    lines = [format_line(d, include_type) for d in low]
    lines.append("\n*To mute a device, open it in Battery Sentinel.*")
    await _fire_persistent(
        "Battery Sentinel: Low Batteries",
        "\n".join(lines),
        notification_id=_LOW_NOTIFICATION_ID,
    )


# ── Per-device email/mobile alert ─────────────────────────────────────

async def fire_low_battery_email(title: str, message: str, settings: dict, device: dict):
    service = settings.get("notify_email_service", "").strip()
    if device.get("notify_email", True) and service:
        addr = device.get("notify_email_address", "") or settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            email_message = message + "\n\nTo mute this device, open it in Battery Sentinel."
            await _fire_notify_service(service, title, email_message, targets)

    if device.get("notify_mobile", False):
        mobile = (device.get("notify_mobile_service", "").strip()
                  or settings.get("notify_mobile_default_service", "").strip())
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


# ── General notification (new device alerts, etc.) ─────────────────────

async def fire_notification(title: str, message: str, settings: dict, device: dict = None, use_bell: bool = True):
    use_bell  = use_bell and (device.get("notify_bell", True) if device else settings.get("notify_persistent", True))
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


# ── Unavailable / recovery notifications ──────────────────────────────

async def fire_unavailable_notification(devices: list, settings: dict):
    title = f"Battery Sentinel: {len(devices)} device(s) went unavailable"
    lines = [f"- {d['name']} ({d['entity_id']})" for d in devices]
    message = "\n".join(lines)

    if settings.get("notify_persistent", True):
        await _fire_persistent(title, message)

    service = settings.get("notify_email_service", "").strip()
    if service:
        addr = settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            html = build_unavailable_html(devices, datetime.datetime.now())
            await _fire_notify_service(service, title, message, targets, html=html)


async def fire_recovery_notification(devices: list, settings: dict):
    title = f"Battery Sentinel: {len(devices)} device(s) back online"
    lines = [f"- {d['name']} ({d['entity_id']})" for d in devices]
    message = "\n".join(lines)

    if settings.get("notify_persistent", True):
        await _fire_persistent(title, message)

    service = settings.get("notify_email_service", "").strip()
    if service:
        addr = settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            html = build_recovery_html(devices, datetime.datetime.now())
            await _fire_notify_service(service, title, message, targets, html=html)


# ── Daily report ───────────────────────────────────────────────────────

async def send_daily_report(devices: list, settings: dict):
    include_all = settings.get("daily_report_include_all", False)
    now = datetime.datetime.now()

    if include_all:
        low = sorted([d for d in devices if device_is_low(d)], key=report_sort_key)
        ok  = sorted([d for d in devices if not device_is_low(d) and d.get("alert_threshold", 15) != -1], key=report_sort_key)
        all_devices = low + ok
    else:
        low = sorted([d for d in devices if device_is_low(d)], key=report_sort_key)
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

    html = build_report_html(low, ok, settings, now, include_all)
    await _fire_notify_service(service, "Battery Sentinel: Daily Battery Report", html, targets, html=html)
    _LOGGER.info("Daily report sent (%d device(s))", len(all_devices))


# ── Script trigger ────────────────────────────────────────────────────

async def fire_script(script_entity_id: str, variables: dict):
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(
                f"{HA_API_URL}/services/script/turn_on",
                headers={**_headers(), "Content-Type": "application/json"},
                json={"entity_id": script_entity_id, "variables": variables},
                timeout=aiohttp.ClientTimeout(total=10),
            )
        _LOGGER.info("Script '%s' fired (entity: %s)", script_entity_id, variables.get("entity_id", "?"))
    except Exception:
        _LOGGER.exception("Failed to fire script '%s'", script_entity_id)


# ── Z-Wave node alerts (per-node, respects per-node channel settings) ──

async def fire_zwave_node_dead(node: dict, settings: dict):
    title = f"Battery Sentinel: Z-Wave node dead — {node['name']}"
    message = f"{node['name']} has gone offline."

    if node.get("notify_bell", True):
        await _fire_persistent(title, message)

    if node.get("notify_email", True):
        service = settings.get("notify_email_service", "").strip()
        if service:
            addr = node.get("notify_email_address", "") or settings.get("notify_email_to", "")
            targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
            cc = settings.get("notify_email_cc", "")
            if cc:
                targets.extend(a.strip() for a in cc.split(",") if a.strip())
            if targets:
                await _fire_notify_service(service, title, message, targets)

    if node.get("notify_mobile", False):
        mobile = settings.get("notify_mobile_default_service", "").strip()
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


async def fire_zwave_node_recovered(node: dict, settings: dict):
    title = f"Battery Sentinel: Z-Wave node back online — {node['name']}"
    message = f"{node['name']} has come back online."

    if node.get("notify_bell", True):
        await _fire_persistent(title, message)

    if node.get("notify_email", True):
        service = settings.get("notify_email_service", "").strip()
        if service:
            addr = node.get("notify_email_address", "") or settings.get("notify_email_to", "")
            targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
            cc = settings.get("notify_email_cc", "")
            if cc:
                targets.extend(a.strip() for a in cc.split(",") if a.strip())
            if targets:
                await _fire_notify_service(service, title, message, targets)

    if node.get("notify_mobile", False):
        mobile = settings.get("notify_mobile_default_service", "").strip()
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


# ── Z-Wave controller / bulk dead alerts ─────────────────────────────

async def fire_zwave_controller_alert(dead_count: int, total: int, settings: dict):
    title = "Battery Sentinel: Z-Wave network disruption"
    message = f"{dead_count} of {total} Z-Wave nodes are offline. This may indicate a Z-Wave controller or service issue."

    if settings.get("notify_persistent", True):
        await _fire_persistent(title, message)

    service = settings.get("notify_email_service", "").strip()
    if service:
        addr = settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            await _fire_notify_service(service, title, message, targets)

    mobile = settings.get("notify_mobile_default_service", "").strip()
    if mobile:
        await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


async def fire_zwave_controller_recovered(alive_count: int, total: int, settings: dict):
    title = "Battery Sentinel: Z-Wave network recovered"
    message = f"{alive_count} of {total} Z-Wave nodes are back online."

    if settings.get("notify_persistent", True):
        await _fire_persistent(title, message)

    service = settings.get("notify_email_service", "").strip()
    if service:
        addr = settings.get("notify_email_to", "")
        targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
        cc = settings.get("notify_email_cc", "")
        if cc:
            targets.extend(a.strip() for a in cc.split(",") if a.strip())
        if targets:
            await _fire_notify_service(service, title, message, targets)

    mobile = settings.get("notify_mobile_default_service", "").strip()
    if mobile:
        await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


# ── Zigbee node alerts ────────────────────────────────────────────────

async def fire_zigbee_node_offline(node: dict, settings: dict):
    title = f"Battery Sentinel: Zigbee device offline — {node['name']}"
    message = f"{node['name']} has not been seen for longer than the configured threshold."

    if node.get("notify_bell", True):
        await _fire_persistent(title, message)

    if node.get("notify_email", True):
        service = settings.get("notify_email_service", "").strip()
        if service:
            addr = node.get("notify_email_address", "") or settings.get("notify_email_to", "")
            targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
            cc = settings.get("notify_email_cc", "")
            if cc:
                targets.extend(a.strip() for a in cc.split(",") if a.strip())
            if targets:
                await _fire_notify_service(service, title, message, targets)

    if node.get("notify_mobile", False):
        mobile = settings.get("notify_mobile_default_service", "").strip()
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


async def fire_zigbee_node_recovered(node: dict, settings: dict):
    title = f"Battery Sentinel: Zigbee device back online — {node['name']}"
    message = f"{node['name']} has come back online."

    if node.get("notify_bell", True):
        await _fire_persistent(title, message)

    if node.get("notify_email", True):
        service = settings.get("notify_email_service", "").strip()
        if service:
            addr = node.get("notify_email_address", "") or settings.get("notify_email_to", "")
            targets = [a.strip() for a in addr.split(",") if a.strip()] if addr else []
            cc = settings.get("notify_email_cc", "")
            if cc:
                targets.extend(a.strip() for a in cc.split(",") if a.strip())
            if targets:
                await _fire_notify_service(service, title, message, targets)

    if node.get("notify_mobile", False):
        mobile = settings.get("notify_mobile_default_service", "").strip()
        if mobile:
            await _fire_notify_service(mobile.removeprefix("notify."), title, message, [])


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
    # Mobile app services don't support the html data field -- skip it to avoid delivery errors
    if service.startswith("mobile_app_"):
        payload = {"title": title, "message": message}
    else:
        # Convert \n to <br> in message so email clients render line breaks correctly
        # even if the service ignores data.html and uses message as the body directly
        html_message = "<br>".join(message.split("\n"))
        payload = {"title": title, "message": html_message}
        payload["data"] = {"html": html} if html else {"html": f"<html><body>{html_message}</body></html>"}
    if targets:
        payload["target"] = targets
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
