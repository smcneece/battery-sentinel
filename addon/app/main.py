import asyncio
import datetime
import json
import logging
import os

from aiohttp import web

import ha_api
import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger(__name__)

VERSION = "2026.04.11"

_cache: list = []
_startup_logged = False
_first_run = True


async def do_refresh():
    global _cache, _startup_logged, _first_run
    _LOGGER.info("Refreshing battery entities from HA")
    live = await ha_api.get_battery_entities()
    metadata = await ha_api.get_entity_metadata()
    for entity in live:
        meta = metadata.get(entity["entity_id"], {})
        entity["area"]      = meta.get("area", "")
        entity["device_id"] = meta.get("device_id", "")

    # Deduplicate battery sensors that share a physical device
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
            # Numeric sensor wins — drop all binary sensors for this device
            skip_eids.update(e["entity_id"] for e in binary)
        elif len(binary) > 1:
            # Only collapse "soon" variants when a "now" variant exists on the same device.
            # Leave all other multi-binary cases alone — they may be different physical batteries
            # sharing one HA device (e.g. a multi-channel hub like Ambient Weather).
            if any("now" in e["name"].lower() for e in binary):
                skip_eids.update(e["entity_id"] for e in binary if "soon" in e["name"].lower())
    if skip_eids:
        live = [e for e in live if e["entity_id"] not in skip_eids]
        _LOGGER.info("Skipped %d redundant battery sensor(s)", len(skip_eids))

    new_eids, _cache = storage.merge_entities(live)
    settings = storage.get_settings()

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
        lines = [ha_api._format_line(d, settings.get("report_include_battery_type", False)) for d in new_devices]
        await ha_api.fire_notification(
            f"Battery Sentinel: {len(new_devices)} new battery device(s) discovered",
            "\n".join(lines),
            settings,
        )

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for device in _cache:
        is_low = ha_api.device_is_low(device)
        if device.get("alert_threshold", 15) == -1:
            if device.get("alert_sent"):
                storage.set_alert_sent(device["entity_id"], False)
            continue

        if is_low and not device.get("alert_sent"):
            _LOGGER.info("Alert: %s is low (%s), sending notifications", device["name"], ha_api.level_str(device))
            await ha_api.fire_low_battery_email(
                "Battery Sentinel: Low battery",
                f"{device['name']} battery is low ({ha_api.level_str(device)}). Threshold: {device.get('alert_threshold', 15)}%",
                settings,
                device,
            )
            storage.set_alert_sent(device["entity_id"], True)
        elif not is_low and device.get("alert_sent"):
            _LOGGER.info("Alert reset: %s recovered (%s)", device["name"], ha_api.level_str(device))
            storage.set_alert_sent(device["entity_id"], False)

        if is_low:
            dev_script = device.get("notify_script", "")
            if dev_script == "__disabled__":
                script = ""
            else:
                script = dev_script or settings.get("notify_script", "")
            if script and device.get("script_last_run") != today:
                _LOGGER.info("Script trigger: %s → %s for %s", script, ha_api.level_str(device), device["name"])
                await ha_api.fire_script(script, device)
                storage.set_script_last_run(device["entity_id"], today)
                for i, d in enumerate(_cache):
                    if d["entity_id"] == device["entity_id"]:
                        _cache[i]["script_last_run"] = today
                        break

    newly_unavailable = []
    for device in _cache:
        is_unavail = device["state"] in ("unavailable", "unknown")
        if is_unavail and not device.get("unavailable_sent"):
            storage.set_unavailable_sent(device["entity_id"], True)
            newly_unavailable.append(device)
        elif not is_unavail and device.get("unavailable_sent"):
            storage.set_unavailable_sent(device["entity_id"], False)
    if newly_unavailable and settings.get("notify_unavailable") and not _first_run:
        _LOGGER.info("Unavailable alert: %d device(s)", len(newly_unavailable))
        await ha_api.fire_unavailable_notification(newly_unavailable, settings)
    elif newly_unavailable and _first_run:
        _LOGGER.info("Suppressed unavailable alert for %d device(s) on first scan (integration startup)", len(newly_unavailable))

    _first_run = False

    await ha_api.update_low_battery_notification(_cache, settings)

    if settings.get("daily_report_enabled"):
        if not settings.get("notify_email_service"):
            _LOGGER.warning("Daily report enabled but no email service configured — skipping")
        else:
            now = datetime.datetime.now()
            try:
                h, m = map(int, settings.get("daily_report_time", "08:00").split(":"))
                report_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                today = now.strftime("%Y-%m-%d")
                if storage.get_last_report_date() == today:
                    _LOGGER.debug("Daily report already sent today, skipping")
                elif now >= report_dt:
                    await ha_api.send_daily_report(_cache, settings)
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
    try:
        data = await request.json()
        result = storage.save_settings(data)
        _LOGGER.info("Settings saved")
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
    asyncio.ensure_future(ha_api.send_daily_report(_cache, settings))
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


async def handle_api_battery_lookup(request):
    try:
        registry = await ha_api.get_device_registry()
        db = await ha_api.fetch_battery_notes_db()
        if not db:
            return web.Response(status=502, text="Could not fetch Battery Notes database")
        auto_fill, conflicts = ha_api.lookup_battery_types(_cache, registry, db)
        for item in auto_fill:
            storage.save_device(item["entity_id"], {"battery_type": item["suggested_type"]})
            for i, d in enumerate(_cache):
                if d["entity_id"] == item["entity_id"]:
                    _cache[i]["battery_type"] = item["suggested_type"]
                    break
        _LOGGER.info("Battery lookup: %d auto-filled, %d conflicts", len(auto_fill), len(conflicts))
        return web.Response(
            text=json.dumps({"auto_fill": auto_fill, "conflicts": conflicts}),
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

    port = int(os.environ.get("INGRESS_PORT", 8099))
    _LOGGER.info("Starting Battery Sentinel on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
