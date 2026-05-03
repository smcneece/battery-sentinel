import asyncio
import datetime
import json
import logging
import os
import zoneinfo

"""Battery Sentinel -- aiohttp web server and main refresh loop.

This module is the orchestrator: it ties together HA data fetching (ha_api),
persistence (storage), and outbound notifications (notifications). Route handlers
are thin -- they delegate to those modules and return JSON or HTML."""

from aiohttp import web

import ha_api
import notifications
import storage
import zwave_monitor
import zigbee_monitor
from device_utils import device_is_low, level_str, format_line

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger(__name__)

VERSION = "2026.05.1"

_cache: list = []
_startup_logged = False
_first_run = True
_zigbee_first_run = True
_ha_tz: datetime.timezone = None


def _local_now() -> datetime.datetime:
    if _ha_tz:
        return datetime.datetime.now(_ha_tz).replace(tzinfo=None)
    return datetime.datetime.now()


async def do_refresh():
    global _cache, _startup_logged, _first_run, _ha_tz
    _LOGGER.info("Refreshing battery entities from HA")
    if _ha_tz is None:
        tz_name = await ha_api.get_ha_timezone()
        if tz_name:
            try:
                _ha_tz = zoneinfo.ZoneInfo(tz_name)
                _LOGGER.info("Using HA timezone: %s", tz_name)
            except Exception:
                _LOGGER.warning("Unknown timezone '%s', falling back to system time", tz_name)
    live = await ha_api.get_battery_entities()
    hidden_eids = await ha_api.get_hidden_entity_ids()
    if hidden_eids:
        before = len(live)
        live = [e for e in live if e["entity_id"] not in hidden_eids]
        skipped = before - len(live)
        if skipped:
            _LOGGER.info("Skipped %d entity/entities hidden in HA entity registry", skipped)
    metadata = await ha_api.get_entity_metadata()
    registry = await ha_api.get_device_registry()
    for entity in live:
        meta = metadata.get(entity["entity_id"], {})
        entity["area"]      = meta.get("area", "")
        entity["device_id"] = meta.get("device_id", "")
        dev = registry.get(entity["device_id"], {}) if entity["device_id"] else {}
        entity["manufacturer"] = dev.get("manufacturer", "")
        entity["model"]        = dev.get("model", "")

    # Deduplicate battery sensors that share a physical device.
    # Some devices expose both a numeric percentage sensor and a binary low/ok sensor.
    # We prefer the numeric one -- it has more information. We also collapse redundant
    # binary "soon" variants when a "now" variant exists on the same device, but leave
    # multi-battery hubs (e.g. Ambient Weather) alone since each binary may be a different battery.
    by_device = {}
    for entity in live:
        did = entity.get("device_id", "")
        if did:
            by_device.setdefault(did, []).append(entity)
    skip_eids = set()
    for entities in by_device.values():
        if len(entities) <= 1:
            continue
        numeric = [e for e in entities if not e["entity_id"].startswith("binary_sensor.")]
        binary  = [e for e in entities if e["entity_id"].startswith("binary_sensor.")]
        if numeric:
            skip_eids.update(e["entity_id"] for e in binary)
        elif len(binary) > 1:
            if any("now" in e["name"].lower() for e in binary):
                skip_eids.update(e["entity_id"] for e in binary if "soon" in e["name"].lower())
    if skip_eids:
        live = [e for e in live if e["entity_id"] not in skip_eids]
        _LOGGER.info("Skipped %d redundant battery sensor(s)", len(skip_eids))

    new_eids, _cache = storage.merge_entities(live)
    settings = storage.get_settings()

    # Log configuration summary once at startup so it's easy to verify settings from the log
    if not _startup_logged:
        _startup_logged = True
        _LOGGER.info(
            "Battery Sentinel v%s — interval=%dmin, email=%s, UI=%s, mobile=%s, daily_report=%s, script=%s",
            VERSION,
            settings.get("check_interval", 10),
            settings.get("notify_email_service") or "none",
            "on" if settings.get("notify_persistent", True) else "off",
            settings.get("notify_mobile_default_service") or "none",
            settings.get("daily_report_time") if settings.get("daily_report_enabled") else "off",
            settings.get("notify_script") or "none",
        )

    if new_eids:
        _LOGGER.info("New device(s) discovered: %s", ", ".join(new_eids))
    if new_eids and settings.get("notify_new_device"):
        new_devices = [d for d in _cache if d["entity_id"] in new_eids]
        lines = [format_line(d, settings.get("report_include_battery_type", False)) for d in new_devices]
        await notifications.fire_notification(
            f"Battery Sentinel: {len(new_devices)} new battery device(s) discovered",
            "\n".join(lines),
            settings,
        )

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for device in _cache:
        is_low = device_is_low(device)
        if device.get("alert_threshold", 15) == -1:
            if device.get("alert_sent"):
                storage.set_alert_sent(device["entity_id"], False)
            continue

        is_muted = False
        muted_until = device.get("muted_until")
        if muted_until:
            try:
                mu_dt = datetime.datetime.fromisoformat(muted_until)
                now_cmp = datetime.datetime.now(datetime.timezone.utc) if mu_dt.tzinfo else datetime.datetime.now()
                if now_cmp < mu_dt:
                    is_muted = True
                else:
                    storage.save_device(device["entity_id"], {"muted_until": None})
                    device["muted_until"] = None
            except Exception:
                pass

        if is_low and not device.get("alert_sent") and not is_muted:
            _LOGGER.info("Alert: %s is low (%s), sending notifications", device["name"], level_str(device))
            await notifications.fire_low_battery_email(
                "Battery Sentinel: Low battery",
                f"{device['name']} battery is low ({level_str(device)}). Threshold: {device.get('alert_threshold', 15)}%",
                settings,
                device,
            )
            storage.set_alert_sent(device["entity_id"], True)
        elif not is_low and device.get("alert_sent"):
            _LOGGER.info("Alert reset: %s recovered (%s)", device["name"], level_str(device))
            storage.set_alert_sent(device["entity_id"], False)
        elif is_low and is_muted:
            _LOGGER.debug("Notifications muted for %s until %s", device["name"], muted_until)

        if is_low and not is_muted:
            dev_script = device.get("notify_script", "")
            if dev_script == "__disabled__":
                script = ""
            else:
                script = dev_script or settings.get("notify_script", "")
            if script and device.get("script_last_run") != today:
                _LOGGER.info("Script trigger: %s → %s for %s", script, level_str(device), device["name"])
                await notifications.fire_script(script, {
                    "device_name":   device.get("name", ""),
                    "battery_level": level_str(device),
                    "battery_type":  device.get("battery_type", ""),
                    "area":          device.get("area", ""),
                    "entity_id":     device["entity_id"],
                    "device_type":   "battery",
                })
                storage.set_script_last_run(device["entity_id"], today)
                for i, d in enumerate(_cache):
                    if d["entity_id"] == device["entity_id"]:
                        _cache[i]["script_last_run"] = today
                        break

    delay_seconds = int(settings.get("notify_unavailable_delay", 5)) * 60
    now = datetime.datetime.now()

    newly_unavailable = []
    newly_recovered = []
    for device in _cache:
        eid = device["entity_id"]
        is_unavail = device["state"] in ("unavailable", "unknown")

        if is_unavail:
            if not device.get("unavailable_since"):
                ts = now.isoformat()
                storage.set_unavailable_since(eid, ts)
                device["unavailable_since"] = ts
            if not device.get("unavailable_sent"):
                try:
                    since = datetime.datetime.fromisoformat(device["unavailable_since"])
                    mu = device.get("muted_until")
                    if mu:
                        mu_dt = datetime.datetime.fromisoformat(mu)
                        now_cmp = datetime.datetime.now(datetime.timezone.utc) if mu_dt.tzinfo else now
                        is_muted_unavail = now_cmp < mu_dt
                    else:
                        is_muted_unavail = False
                    if (now - since).total_seconds() >= delay_seconds and not is_muted_unavail:
                        storage.set_unavailable_sent(eid, True)
                        device["unavailable_sent"] = True
                        newly_unavailable.append(device)
                except Exception:
                    pass
        else:
            if device.get("unavailable_since"):
                storage.set_unavailable_since(eid, None)
                device["unavailable_since"] = None
            if device.get("unavailable_sent"):
                storage.set_unavailable_sent(eid, False)
                device["unavailable_sent"] = False
                newly_recovered.append(device)

    if settings.get("suppress_unavailable_if_monitored", True):
        if settings.get("zwave_monitor_enabled") or settings.get("zigbee_monitor_enabled"):
            try:
                monitored_ids = await ha_api.get_monitored_entity_device_ids()
                if monitored_ids:
                    before = len(newly_unavailable)
                    newly_unavailable = [d for d in newly_unavailable if d.get("device_id") not in monitored_ids]
                    newly_recovered   = [d for d in newly_recovered   if d.get("device_id") not in monitored_ids]
                    suppressed = before - len(newly_unavailable)
                    if suppressed:
                        _LOGGER.info("Suppressed %d unavailable alert(s) for Z-Wave/Zigbee monitored device(s)", suppressed)
            except Exception:
                _LOGGER.exception("Failed to suppress monitored device unavailable alerts")

    # Suppress unavailable alerts on the very first scan -- devices that were already
    # unavailable before the add-on started would otherwise fire immediately on boot.
    if newly_unavailable and settings.get("notify_unavailable") and not _first_run:
        _LOGGER.info("Unavailable alert: %d device(s)", len(newly_unavailable))
        await notifications.fire_unavailable_notification(newly_unavailable, settings)
    elif newly_unavailable and _first_run:
        _LOGGER.info("Suppressed unavailable alert for %d device(s) on first scan (integration startup)", len(newly_unavailable))

    if newly_recovered and settings.get("notify_unavailable"):
        _LOGGER.info("Recovery alert: %d device(s) back online", len(newly_recovered))
        await notifications.fire_recovery_notification(newly_recovered, settings)

    this_run_is_first = _first_run
    _first_run = False

    await notifications.update_low_battery_notification(_cache, settings)

    if settings.get("zwave_monitor_enabled"):
        await zwave_monitor.check_nodes(settings, this_run_is_first, metadata)

    if settings.get("daily_report_enabled"):
        if not settings.get("notify_email_service"):
            _LOGGER.warning("Daily report enabled but no email service configured — skipping")
        else:
            now = _local_now()
            try:
                report_days = settings.get("daily_report_days", list(range(7)))
                if now.weekday() not in report_days:
                    _LOGGER.debug("Daily report not scheduled for today (%s), skipping", now.strftime("%A"))
                else:
                    h, m = map(int, settings.get("daily_report_time", "08:00").split(":"))
                    report_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                    today = now.strftime("%Y-%m-%d")
                    if storage.get_last_report_date() == today:
                        _LOGGER.debug("Daily report already sent today, skipping")
                    elif now >= report_dt:
                        await notifications.send_daily_report(_cache, settings)
                        storage.set_last_report_date(today)
            except Exception:
                _LOGGER.exception("Daily report check failed")


async def refresh_loop():
    while True:
        await do_refresh()
        settings = storage.get_settings()
        interval_min = max(1, int(settings.get("check_interval", 10)))
        _LOGGER.info("Next check in %d minute(s)", interval_min)
        await asyncio.sleep(interval_min * 60)


async def zigbee_loop():
    global _zigbee_first_run
    while True:
        settings = storage.get_settings()
        if settings.get("zigbee_monitor_enabled"):
            await zigbee_monitor.check_nodes(settings, _zigbee_first_run)
            _zigbee_first_run = False
        interval_min = max(1, int(settings.get("zigbee_scan_interval", 30)))
        await asyncio.sleep(interval_min * 60)


async def handle_index(request):
    ingress_path = request.headers.get("X-Ingress-Path", "").rstrip("/")
    return web.Response(text=_build_html(ingress_path), content_type="text/html")


async def handle_icon(request):
    return web.FileResponse("/app/icon.png")


async def handle_api_batteries(request):
    return web.Response(text=json.dumps(_cache), content_type="application/json")


async def handle_api_settings_get(request):
    return web.Response(text=json.dumps(storage.get_settings()), content_type="application/json")


async def handle_api_settings_post(request):
    global _zigbee_first_run
    try:
        data = await request.json()
        old_settings = storage.get_settings()
        result = storage.save_settings(data)
        _LOGGER.info("Settings saved")
        # Trigger immediate Zigbee scan when monitoring is first enabled
        if result.get("zigbee_monitor_enabled") and not old_settings.get("zigbee_monitor_enabled"):
            _zigbee_first_run = False
            asyncio.ensure_future(zigbee_monitor.check_nodes(result, False))
        return web.Response(text=json.dumps(result), content_type="application/json")
    except Exception:
        _LOGGER.exception("Failed to save settings")
        return web.Response(status=400, text="Bad request")


async def handle_api_scan(request):
    _LOGGER.info("Manual scan requested")
    asyncio.ensure_future(do_refresh())
    return web.Response(text='{"status":"ok"}', content_type="application/json")


async def handle_api_report_now(request):
    settings = storage.get_settings()
    if not settings.get("notify_email_service"):
        return web.Response(status=400, text="No email service configured")
    _LOGGER.info("Manual daily report requested")
    asyncio.ensure_future(notifications.send_daily_report(_cache, settings))
    return web.Response(text='{"status":"ok"}', content_type="application/json")


async def handle_api_notify_services(request):
    services = await ha_api.get_notify_services()
    return web.Response(text=json.dumps(services), content_type="application/json")


async def handle_api_scripts(request):
    scripts = await ha_api.get_scripts()
    return web.Response(text=json.dumps(scripts), content_type="application/json")


async def handle_api_rename(request):
    entity_id = request.match_info["entity_id"]
    try:
        data = await request.json()
        new_name = data.get("name", "").strip()
        if not new_name:
            return web.Response(status=400, text="Name required")
        success = await ha_api.rename_entity(entity_id, new_name)
        if not success:
            return web.Response(status=502, text="HA rename failed")
        global _cache
        for i, d in enumerate(_cache):
            if d["entity_id"] == entity_id:
                _cache[i]["name"] = new_name
                break
        return web.Response(text='{"status":"ok"}', content_type="application/json")
    except Exception:
        _LOGGER.exception("Failed to rename entity %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_device_delete(request):
    entity_id = request.match_info["entity_id"]
    try:
        storage.delete_device(entity_id)
        global _cache
        _cache = [d for d in _cache if d["entity_id"] != entity_id]
        return web.Response(text='{"status":"ok"}', content_type="application/json")
    except Exception:
        _LOGGER.exception("Failed to hide device %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_hidden_devices(request):
    return web.Response(text=json.dumps(storage.get_hidden_devices()), content_type="application/json")


async def handle_api_device_purge(request):
    entity_id = request.match_info["entity_id"]
    try:
        storage.purge_device(entity_id)
        return web.Response(text='{"status":"ok"}', content_type="application/json")
    except Exception:
        _LOGGER.exception("Failed to purge device %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_device_restore(request):
    entity_id = request.match_info["entity_id"]
    try:
        storage.restore_device(entity_id)
        asyncio.ensure_future(do_refresh())
        return web.Response(text='{"status":"ok"}', content_type="application/json")
    except Exception:
        _LOGGER.exception("Failed to restore device %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_zwave_nodes(request):
    nodes = storage.get_zwave_nodes()
    areas = await ha_api.get_zwave_node_areas()
    result = []
    for eid, node in nodes.items():
        node["entity_id"] = eid
        node["area"] = areas.get(eid, node.get("area", ""))
        result.append(node)
    return web.Response(text=json.dumps(result), content_type="application/json")


async def handle_api_zwave_node_post(request):
    entity_id = request.match_info["entity_id"]
    try:
        data = await request.json()
        node = storage.save_zwave_node(entity_id, data)
        return web.Response(text=json.dumps(node), content_type="application/json")
    except KeyError:
        return web.Response(status=404, text="Z-Wave node not found")
    except Exception:
        _LOGGER.exception("Failed to save Z-Wave node %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_zigbee_nodes(request):
    live = await ha_api.get_zigbee_last_seen_entities()
    areas = await ha_api.get_zigbee_node_areas()
    merged = storage.merge_zigbee_nodes(live)
    for node in merged:
        eid = node.get("entity_id", "")
        node["area"] = areas.get(eid, node.get("area", ""))
    return web.Response(text=json.dumps(merged), content_type="application/json")


async def handle_api_zigbee_node_post(request):
    entity_id = request.match_info["entity_id"]
    try:
        data = await request.json()
        node = storage.save_zigbee_node(entity_id, data)
        return web.Response(text=json.dumps(node), content_type="application/json")
    except KeyError:
        return web.Response(status=404, text="Zigbee node not found")
    except Exception:
        _LOGGER.exception("Failed to save Zigbee node %s", entity_id)
        return web.Response(status=400, text="Bad request")


async def handle_api_zigbee_scan(request):
    settings = storage.get_settings()
    if not settings.get("zigbee_monitor_enabled"):
        return web.Response(status=400, text="Zigbee monitoring is not enabled")
    _LOGGER.info("Manual Zigbee scan requested")
    asyncio.ensure_future(zigbee_monitor.check_nodes(settings, False))
    return web.Response(text='{"status":"ok"}', content_type="application/json")


async def handle_api_battery_lookup(request):
    try:
        registry = await ha_api.get_device_registry()
        db = ha_api.fetch_battery_notes_db()
        if not db:
            return web.Response(status=502, text="Could not load Battery Notes database")
        auto_fill, conflicts = ha_api.lookup_battery_types(_cache, registry, db)
        for item in auto_fill:
            storage.save_device(item["entity_id"], {"battery_type": item["suggested_type"]})
            for i, d in enumerate(_cache):
                if d["entity_id"] == item["entity_id"]:
                    _cache[i]["battery_type"] = item["suggested_type"]
                    break

        # Add any new types from both auto_fill and conflicts to the battery_types list.
        # Also sweep the cache for orphaned types (set on devices but missing from the list)
        # so that types added by a previous broken run get cleaned up too.
        settings = storage.get_settings()
        existing = set(settings.get("battery_types", []))
        seen: set = set()
        new_types = []
        for item in auto_fill + conflicts:
            t = item["suggested_type"]
            if t and t not in existing and t not in seen:
                new_types.append(t)
                seen.add(t)
        for device in _cache:
            t = device.get("battery_type", "")
            if t and t.upper() != "MANUAL" and t not in existing and t not in seen:
                new_types.append(t)
                seen.add(t)
        updated_types = settings.get("battery_types", [])
        if new_types:
            updated_types = updated_types + new_types
            storage.save_settings({"battery_types": updated_types})

        _LOGGER.info("Battery lookup: %d auto-filled, %d conflicts, %d new types added",
                     len(auto_fill), len(conflicts), len(new_types))
        return web.Response(
            text=json.dumps({
                "auto_fill": auto_fill,
                "conflicts": conflicts,
                "updated_types": updated_types,
            }),
            content_type="application/json",
        )
    except Exception:
        _LOGGER.exception("Battery lookup failed")
        return web.Response(status=500, text="Lookup failed")


async def handle_api_device_post(request):
    entity_id = request.match_info["entity_id"]
    try:
        data = await request.json()
        device = storage.save_device(entity_id, data)
        global _cache
        for i, d in enumerate(_cache):
            if d["entity_id"] == entity_id:
                _cache[i] = {**_cache[i], **device}
                break
        return web.Response(text=json.dumps(device), content_type="application/json")
    except KeyError:
        return web.Response(status=404, text="Device not found")
    except Exception:
        _LOGGER.exception("Failed to save device %s", entity_id)
        return web.Response(status=400, text="Bad request")


def _build_html(base: str) -> str:
    with open("/app/index.html", "r") as f:
        template = f.read()
    return template.replace("{{BASE}}", base).replace("{{VERSION}}", VERSION)



async def on_startup(app):
    asyncio.ensure_future(refresh_loop())
    asyncio.ensure_future(zigbee_loop())


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get("/",                          handle_index)
    app.router.add_get("/icon.png",                  handle_icon)
    app.router.add_get("/api/batteries",             handle_api_batteries)
    app.router.add_get("/api/settings",              handle_api_settings_get)
    app.router.add_post("/api/settings",             handle_api_settings_post)
    app.router.add_post("/api/scan",                 handle_api_scan)
    app.router.add_post("/api/report-now",           handle_api_report_now)
    app.router.add_get("/api/notify-services",       handle_api_notify_services)
    app.router.add_get("/api/scripts",               handle_api_scripts)
    app.router.add_post("/api/device/{entity_id}",          handle_api_device_post)
    app.router.add_delete("/api/device/{entity_id}",        handle_api_device_delete)
    app.router.add_post("/api/device/{entity_id}/restore",  handle_api_device_restore)
    app.router.add_delete("/api/device/{entity_id}/purge",  handle_api_device_purge)
    app.router.add_get("/api/hidden-devices",               handle_api_hidden_devices)
    app.router.add_post("/api/battery-lookup",              handle_api_battery_lookup)
    app.router.add_post("/api/rename/{entity_id}",          handle_api_rename)
    app.router.add_get("/api/zwave-nodes",                  handle_api_zwave_nodes)
    app.router.add_post("/api/zwave-node/{entity_id}",      handle_api_zwave_node_post)
    app.router.add_get("/api/zigbee-nodes",                 handle_api_zigbee_nodes)
    app.router.add_post("/api/zigbee-node/{entity_id}",     handle_api_zigbee_node_post)
    app.router.add_post("/api/zigbee-scan",                 handle_api_zigbee_scan)

    port = int(os.environ.get("INGRESS_PORT", 8099))
    _LOGGER.info("Starting Battery Sentinel on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
