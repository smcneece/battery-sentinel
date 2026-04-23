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

VERSION = "2026.04.6"

_cache: list = []
_startup_logged = False


async def do_refresh():
    global _cache, _startup_logged
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

    await ha_api.update_low_battery_notification(_cache, settings)

    if settings.get("daily_report_enabled") and settings.get("notify_email_service"):
        now = datetime.datetime.now()
        try:
            h, m = map(int, settings.get("daily_report_time", "08:00").split(":"))
            report_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
            today = now.strftime("%Y-%m-%d")
            if now >= report_dt and storage.get_last_report_date() != today:
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
        _LOGGER.exception("Failed to delete device %s", entity_id)
        return web.Response(status=400, text="Bad request")


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
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Battery Sentinel</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: var(--primary-font-family, sans-serif); background: #111; color: #e0e0e0; }}

  .header {{ padding: 1rem 1rem .5rem; display: flex; align-items: center; gap: .6rem; }}
  .header h1 {{ font-size: 1.3rem; color: #fff; }}
  .badge {{ font-size: .7rem; padding: .15rem .45rem; border-radius: 3px; background: #2a2a2a; color: #888; border: 1px solid #333; }}

  .tabs {{ display: flex; border-bottom: 1px solid #2a2a2a; padding: 0 1rem; }}
  .tab-btn {{ background: none; border: none; color: #888; padding: .55rem 1rem; cursor: pointer; font-size: .9rem; border-bottom: 2px solid transparent; margin-bottom: -1px; transition: color .15s; }}
  .tab-btn.active {{ color: #58a6ff; border-bottom-color: #58a6ff; }}
  .tab-btn:hover {{ color: #ccc; }}

  .tab-content {{ display: none; padding: 1rem; }}
  .tab-content.active {{ display: block; }}

  table {{ width: 100%; border-collapse: collapse; font-size: .9rem; table-layout: fixed; }}
  th {{ position: relative; text-align: left; padding: .5rem .75rem; color: #888; border-bottom: 1px solid #2a2a2a; font-weight: 500; cursor: pointer; user-select: none; white-space: nowrap; overflow: visible; }}
  th:hover {{ color: #fff; }}
  th .sort-arrow {{ font-size: .65rem; color: #58a6ff; margin-left: .25rem; }}
  td {{ padding: .5rem .75rem; border-bottom: 1px solid #1a1a1a; vertical-align: middle; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  tr.device-row {{ cursor: pointer; }}
  tr.device-row:hover td {{ background: #181818; }}
  .resize-handle {{ position: absolute; right: -5px; top: 10%; width: 14px; height: 80%; cursor: col-resize; z-index: 2; display: flex; align-items: center; justify-content: center; }}
  .resize-handle::after {{ content: ''; display: block; width: 3px; height: 100%; background: #404040; border-radius: 2px; transition: background .15s; }}
  .resize-handle:hover::after, .resize-handle.dragging::after {{ background: #58a6ff; }}
  .inline-select {{ background: transparent; border: 1px solid #2a2a2a; border-radius: 3px; color: #888; font-size: .8rem; padding: .15rem .25rem; cursor: pointer; width: auto; }}
  .inline-select:hover {{ border-color: #444; color: #ccc; }}
  .inline-select:focus {{ outline: none; border-color: #58a6ff; }}
  .inline-select option {{ background: #1a1a1a; }}

  .bar-wrap {{ width: 100px; height: 8px; background: #2a2a2a; border-radius: 4px; display: inline-block; vertical-align: middle; overflow: hidden; }}
  .bar {{ display: block; height: 100%; border-radius: 4px; }}
  .pct {{ display: inline-block; width: 3rem; text-align: right; margin-right: .5rem; font-variant-numeric: tabular-nums; }}
  .critical {{ color: #f55; }}
  .warning  {{ color: #fa0; }}
  .ok       {{ color: #4c4; }}
  .muted    {{ color: #555; font-size: .8rem; }}

  .modal-overlay {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,.75); z-index: 100; align-items: center; justify-content: center; }}
  .modal-overlay.open {{ display: flex; }}
  .modal-box {{ background: #1a1a1a; border: 1px solid #333; border-radius: 8px; width: 500px; max-width: 95vw; max-height: 90vh; overflow-y: auto; }}
  .modal-header {{ display: flex; align-items: center; justify-content: space-between; padding: 1rem 1.25rem; border-bottom: 1px solid #2a2a2a; }}
  .modal-header h2 {{ font-size: 1.05rem; color: #fff; }}
  .modal-close {{ background: none; border: none; color: #666; font-size: 1.5rem; cursor: pointer; line-height: 1; padding: 0 .1rem; }}
  .modal-close:hover {{ color: #ccc; }}
  .modal-body {{ padding: 1.25rem; display: flex; flex-direction: column; gap: .9rem; }}
  .modal-footer {{ padding: .85rem 1.25rem; border-top: 1px solid #2a2a2a; display: flex; gap: .5rem; justify-content: flex-end; }}

  .entity-id {{ font-size: .75rem; color: #555; font-family: monospace; word-break: break-all; }}
  .modal-name-input {{ background: transparent; border: none; border-bottom: 1px dashed transparent; color: #fff; font-size: 1.05rem; font-weight: 500; font-family: inherit; padding: 0 0 2px; width: 100%; cursor: text; }}
  .modal-name-input:hover {{ border-bottom-color: #555; }}
  .modal-name-input:focus {{ outline: none; border-bottom: 1px solid #58a6ff; background: transparent; }}
  .level-display {{ font-size: .9rem; }}

  .field {{ display: flex; flex-direction: column; gap: .3rem; }}
  label {{ font-size: .8rem; color: #888; }}
  .field-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }}

  input, select, textarea {{
    background: #222; border: 1px solid #3a3a3a; border-radius: 4px;
    color: #e0e0e0; padding: .4rem .6rem; font-size: .88rem; width: 100%;
  }}
  input:focus, select:focus, textarea:focus {{ outline: none; border-color: #58a6ff; }}
  textarea {{ resize: vertical; min-height: 72px; font-family: inherit; }}
  select option {{ background: #222; }}

  .btn {{ padding: .4rem .9rem; border-radius: 4px; border: none; cursor: pointer; font-size: .85rem; transition: background .15s; }}
  .btn-primary   {{ background: #1a4a8a; color: #e0e0e0; }}
  .btn-primary:hover   {{ background: #1e5aa0; }}
  .btn-secondary {{ background: #2a2a2a; color: #ccc; border: 1px solid #3a3a3a; }}
  .btn-secondary:hover {{ background: #333; }}
  .btn-replaced  {{ background: #1a3a1a; color: #5c5; border: 1px solid #2a4a2a; width: 100%; padding: .5rem; font-size: .88rem; }}
  .btn-replaced:hover  {{ background: #223a22; }}
  .btn-danger    {{ background: #3a1a1a; color: #f55; border: 1px solid #5a2a2a; }}
  .btn-danger:hover    {{ background: #4a2020; }}

  .settings-grid {{ display: flex; flex-wrap: wrap; gap: 1rem; align-items: flex-start; }}
  .settings-card {{ flex: 1 1 300px; background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 8px; padding: 1.25rem; }}
  .settings-card h3 {{ font-size: .9rem; color: #aaa; text-transform: uppercase; letter-spacing: .04em; margin-bottom: .85rem; padding-bottom: .5rem; border-bottom: 1px solid #2a2a2a; }}
  .setting-row {{ display: flex; flex-direction: column; gap: .3rem; margin-bottom: .75rem; }}
  .setting-row p {{ font-size: .82rem; color: #666; }}
  .type-list {{ list-style: none; display: flex; flex-direction: column; gap: .25rem; margin-bottom: .5rem; }}
  .type-item {{ display: flex; align-items: center; justify-content: space-between; background: #1e1e1e; border: 1px solid #2a2a2a; border-radius: 4px; padding: .3rem .6rem; font-size: .88rem; }}
  .type-delete {{ background: none; border: none; color: #555; cursor: pointer; font-size: 1rem; line-height: 1; padding: 0 .15rem; }}
  .type-delete:hover {{ color: #f55; }}
  .type-add {{ display: flex; gap: .5rem; margin-top: .25rem; }}
  .type-add input {{ flex: 1; }}
  .settings-footer {{ margin-top: 1.25rem; display: flex; align-items: center; justify-content: center; gap: .75rem; padding-top: 1rem; border-top: 1px solid #2a2a2a; }}
  .save-status {{ font-size: .82rem; color: #4c4; }}
  .notify-row {{ display: flex; align-items: center; gap: .6rem; margin-bottom: .5rem; font-size: .9rem; }}
  .notify-row input[type=checkbox] {{ width: auto; accent-color: #58a6ff; }}
  .notify-check {{ display: inline-flex; align-items: center; gap: .25rem; font-size: .78rem; color: #aaa; cursor: pointer; margin-right: .5rem; white-space: nowrap; }}
  .notify-check input[type=checkbox] {{ width: auto; accent-color: #58a6ff; margin: 0; }}
  .scan-bar {{ display: flex; align-items: center; gap: .75rem; margin-bottom: .75rem; }}
  .scan-status {{ font-size: .82rem; color: #888; }}

  .device-toolbar {{ padding: .5rem 1rem .25rem; }}
  #device-search {{ max-width: 260px; padding: .35rem .6rem; font-size: .88rem; }}
  .loading {{ color: #666; padding: 2rem; text-align: center; }}
  .error   {{ color: #f55; padding: 2rem; text-align: center; }}
</style>
</head>
<body>

<div class="header">
  <h1>Battery Sentinel</h1>
  <span class="badge">{VERSION}</span>
</div>

<nav class="tabs">
  <button class="tab-btn active" data-tab="devices">Devices</button>
  <button class="tab-btn" data-tab="settings">Settings</button>
</nav>

<div id="tab-devices" class="tab-content active">
  <div class="device-toolbar">
    <input type="text" id="device-search" placeholder="Filter devices..." autocomplete="off">
  </div>
  <div id="content"><p class="loading">Loading devices...</p></div>
</div>

<div id="tab-settings" class="tab-content">
  <div class="settings-grid">

    <div class="settings-card">
      <h3>General</h3>
      <div class="scan-bar" style="margin-bottom:.6rem">
        <button class="btn btn-secondary" onclick="scanNow()">Scan Now</button>
        <span class="scan-status" id="scan-status"></span>
      </div>
      <p class="muted" style="font-size:.82rem;margin-bottom:1.25rem">Refreshes automatically per the check interval. Use Scan Now to refresh immediately.</p>
      <div class="setting-row">
        <label>Check interval (minutes)</label>
        <p>How often to scan and update the low-battery notification.</p>
        <input type="number" id="setting-check-interval" min="1" max="120" style="max-width:80px" value="10">
      </div>
      <div class="setting-row">
        <label>Battery Types</label>
        <p>Available in the per-device battery type dropdown. Add or remove as needed.</p>
      </div>
      <ul class="type-list" id="type-list"></ul>
      <div class="type-add">
        <input type="text" id="type-input" placeholder="Add type, e.g. CR2477">
        <button class="btn btn-secondary" onclick="addType()">Add</button>
      </div>
    </div>

    <div class="settings-card">
      <h3>Notifications</h3>
      <div class="setting-row">
        <label>Default alert threshold</label>
        <p>Applied to any newly discovered device.</p>
        <select id="setting-threshold" style="max-width:180px">
          <option value="5">5%</option>
          <option value="10">10%</option>
          <option value="15">15%</option>
          <option value="20">20%</option>
          <option value="25">25%</option>
          <option value="30">30%</option>
          <option value="-1">Ignore</option>
        </select>
      </div>
      <div class="notify-row">
        <input type="checkbox" id="setting-notify-persistent">
        <label for="setting-notify-persistent">UI notification (HA persistent) for low batteries</label>
      </div>
      <div class="setting-row" style="margin-top:.5rem;margin-bottom:.5rem">
        <label>Default mobile service</label>
        <p>Used when a device has Mobile enabled but no specific service set.</p>
        <select id="setting-mobile-default">
          <option value="">-- None --</option>
        </select>
      </div>
      <div class="setting-row" style="margin-bottom:.5rem">
        <label>Global script trigger</label>
        <p>Script to run when any device crosses its threshold. Per-device script overrides this.</p>
        <select id="setting-notify-script">
          <option value="">-- None --</option>
        </select>
      </div>
      <div class="notify-row" style="margin-bottom:.85rem">
        <input type="checkbox" id="setting-notify-new-device">
        <label for="setting-notify-new-device">Alert when a new battery device is discovered</label>
      </div>
      <div class="setting-row">
        <label>Email notify service</label>
        <p>Which HA notify service sends email alerts.</p>
        <select id="setting-notify-service">
          <option value="">-- None --</option>
        </select>
      </div>
      <div class="setting-row">
        <label>Default To address</label>
        <input type="email" id="setting-notify-to" placeholder="you@example.com">
      </div>
      <div class="setting-row">
        <label>CC addresses</label>
        <p>Separate multiple addresses with commas.</p>
        <input type="text" id="setting-notify-cc" placeholder="person@example.com, other@example.com">
      </div>
    </div>

    <div class="settings-card">
      <h3>Daily Report</h3>
      <div class="notify-row">
        <input type="checkbox" id="setting-daily-enabled">
        <label for="setting-daily-enabled">Send daily battery report email</label>
      </div>
      <div class="setting-row" style="margin-top:.85rem">
        <label>Send time</label>
        <input type="time" id="setting-daily-time" style="max-width:130px" value="08:00">
      </div>
      <div class="setting-row">
        <label>Include in report</label>
        <select id="setting-daily-include">
          <option value="low">Low batteries only</option>
          <option value="all">All batteries (full status)</option>
        </select>
      </div>
      <div class="notify-row" style="margin-top:.5rem">
        <input type="checkbox" id="setting-include-battery-type">
        <label for="setting-include-battery-type">Include battery type in reports and notifications</label>
      </div>
    </div>

  </div>
  <div class="settings-footer">
    <button class="btn btn-primary" onclick="saveSettings()">Save Settings</button>
    <span class="save-status" id="settings-status"></span>
  </div>
</div>

<div class="modal-overlay" id="modal">
  <div class="modal-box">
    <div class="modal-header">
      <div style="flex:1;min-width:0">
        <input type="text" class="modal-name-input" id="modal-name-input" title="Click to rename in Home Assistant">
        <div class="entity-id" id="modal-entity" style="margin-top:.2rem"></div>
      </div>
      <button class="modal-close" onclick="closeModal()" title="Close">&times;</button>
    </div>
    <div class="modal-body">
      <div class="field-row">
        <div class="field">
          <label>Battery Type</label>
          <select id="modal-type"></select>
        </div>
        <div class="field">
          <label>Alert Threshold</label>
          <select id="modal-threshold">
            <option value="5">5%</option>
            <option value="10">10%</option>
            <option value="15">15%</option>
            <option value="20">20%</option>
            <option value="25">25%</option>
            <option value="30">30%</option>
            <option value="-1">Ignore</option>
          </select>
        </div>
      </div>
      <div class="field">
        <label>Notes</label>
        <textarea id="modal-notes" placeholder="Optional notes about this device or its battery..."></textarea>
      </div>
      <div class="field">
        <label>Notifications</label>
        <div style="display:flex;gap:1.25rem;margin-top:.25rem">
          <label class="notify-check"><input type="checkbox" id="modal-notify-bell"> UI (HA)</label>
          <label class="notify-check"><input type="checkbox" id="modal-notify-email"> Email</label>
          <label class="notify-check"><input type="checkbox" id="modal-notify-mobile"> Mobile</label>
        </div>
      </div>
      <div class="field">
        <label>Email address override</label>
        <input type="email" id="modal-notify-addr" placeholder="Leave blank to use global default">
      </div>
      <div class="field">
        <label>Mobile app notification</label>
        <select id="modal-mobile-service">
          <option value="">-- Disabled --</option>
        </select>
      </div>
      <div class="field">
        <label>Script trigger</label>
        <select id="modal-notify-script">
          <option value="">-- Use global default --</option>
          <option value="__disabled__">-- Disabled for this device --</option>
        </select>
      </div>
      <div class="field">
        <label>Last Replaced</label>
        <input type="date" id="modal-replaced">
      </div>
      <button class="btn btn-replaced" onclick="markReplacedToday()">Replaced / Recharged Today</button>
    </div>
    <div class="modal-footer" style="display:block;padding:.85rem 1.25rem">
      <div id="modal-footer-normal" style="display:flex;justify-content:space-between;align-items:center">
        <button class="btn btn-danger" onclick="confirmDelete()">Delete</button>
        <div style="display:flex;gap:.5rem">
          <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
          <button class="btn btn-primary" onclick="saveDevice()">Save</button>
        </div>
      </div>
      <div id="modal-footer-confirm" style="display:none;justify-content:space-between;align-items:center">
        <span style="color:#f88;font-size:.85rem">Remove from Battery Sentinel?</span>
        <div style="display:flex;gap:.5rem">
          <button class="btn btn-secondary" onclick="cancelDelete()">Keep</button>
          <button class="btn btn-danger" onclick="deleteDevice()">Yes, Delete</button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
const BASE = "{base}";
let _devices        = [];
let _settings       = {{}};
let _notifyServices = [];
let _scripts        = [];
let _sortCol        = "level";
let _sortDir        = 1;
let _activeEntity   = null;
const _colDefaults  = {{ name: 22, level: 16, alert: 11, notify: 19, script: 16, area: 16 }};
let _colWidths = Object.assign({{}}, _colDefaults, JSON.parse(localStorage.getItem('bs_colWidths_v4') || '{{}}'));
let _resizing    = null;
let _wasDragging = false;

// ── Column resize ─────────────────────────────────────────────────────
function startResize(e, col) {{
  e.preventDefault();
  e.stopPropagation();
  const table = document.querySelector("table");
  if (!table) return;
  const th = document.querySelector(`th[data-col="${{col}}"]`);
  _resizing    = {{ col, startX: e.clientX, startW: th.offsetWidth, tableW: table.offsetWidth }};
  _wasDragging = false;
  document.querySelector(`.resize-handle[data-col="${{col}}"]`).classList.add("dragging");
  document.addEventListener("mousemove", onResize);
  document.addEventListener("mouseup",   stopResize);
}}

function onResize(e) {{
  if (!_resizing) return;
  const delta = e.clientX - _resizing.startX;
  if (Math.abs(delta) < 3) return;
  _wasDragging = true;
  const newPct = Math.max(8, (_resizing.startW + delta) / _resizing.tableW * 100);
  _colWidths[_resizing.col] = parseFloat(newPct.toFixed(1));
  const col = document.querySelector(`col[data-col="${{_resizing.col}}"]`);
  if (col) col.style.width = _colWidths[_resizing.col] + "%";
}}

function stopResize() {{
  if (_resizing) {{
    document.querySelector(`.resize-handle[data-col="${{_resizing.col}}"]`)?.classList.remove("dragging");
    _resizing = null;
    localStorage.setItem('bs_colWidths_v4', JSON.stringify(_colWidths));
  }}
  document.removeEventListener("mousemove", onResize);
  document.removeEventListener("mouseup",   stopResize);
}}

// ── Tabs ──────────────────────────────────────────────────────────────
document.querySelectorAll(".tab-btn").forEach(btn => {{
  btn.addEventListener("click", () => {{
    document.querySelectorAll(".tab-btn, .tab-content").forEach(el => el.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  }});
}});

// ── Helpers ───────────────────────────────────────────────────────────
function sortKey(d, col) {{
  if (col === "name")  return d.name.toLowerCase();
  if (col === "area")  return (d.area || "zzz").toLowerCase();
  if (d.entity_id.startsWith("binary_sensor.")) return d.state === "on" ? 0 : 100;
  const n = parseInt(d.state, 10);
  return isNaN(n) ? 999 : n;
}}

function levelCell(d) {{
  if (d.entity_id.startsWith("binary_sensor.")) {{
    const low = d.state === "on";
    return `<span class="pct ${{low ? 'critical' : 'ok'}}">${{low ? 'Low' : 'OK  '}}</span>
            <span class="bar-wrap"><span class="bar" style="width:100%;background:${{low?'#f55':'#4c4'}}"></span></span>`;
  }}
  const pct = parseInt(d.state, 10);
  const cls = pct < 10 ? "critical" : pct < 25 ? "warning" : "ok";
  const col = pct < 10 ? "#f55"     : pct < 25 ? "#fa0"    : "#4c4";
  return `<span class="pct ${{cls}}">${{pct}}%</span>
          <span class="bar-wrap"><span class="bar" style="width:${{pct}}%;background:${{col}}"></span></span>`;
}}

// ── Devices table ─────────────────────────────────────────────────────
function renderTable() {{
  const query = (document.getElementById("device-search")?.value || '').trim().toLowerCase();
  const visible = query ? _devices.filter(d => d.name.toLowerCase().includes(query)) : _devices;
  const sorted = [...visible].sort((a, b) => {{
    const ka = sortKey(a, _sortCol), kb = sortKey(b, _sortCol);
    return ka < kb ? -_sortDir : ka > kb ? _sortDir : 0;
  }});

  const thCls = col => _sortCol === col ? (_sortDir === 1 ? "sort-asc" : "sort-desc") : "";
  const arrow = col => _sortCol === col ? `<span class="sort-arrow">${{_sortDir===1?'▲':'▼'}}</span>` : '';

  const thrOptions = v => [5,10,15,20,25,30].map(n =>
    `<option value="${{n}}"${{v===n?' selected':''}}>${{n}}%</option>`
  ).join('') + `<option value="-1"${{v===-1?' selected':''}}>Ignore</option>`;

  const rows = sorted.map(d => {{
    const ignored = d.alert_threshold === -1;
    const dim = ignored ? ' style="opacity:.4"' : '';
    const isBinary = d.entity_id.startsWith("binary_sensor.");
    const thrCell = isBinary
      ? `<option value="15"${{d.alert_threshold !== -1 ? ' selected' : ''}}>Monitor</option>
         <option value="-1"${{d.alert_threshold === -1 ? ' selected' : ''}}>Ignore</option>`
      : thrOptions(d.alert_threshold);
    const scriptLabel = (() => {{
      const ds = d.notify_script || '';
      if (ds === '__disabled__') return '<span class="muted">Off</span>';
      if (ds) {{ const s = _scripts.find(x => x.entity_id === ds); return `<span style="color:#aaa;font-size:.8rem">${{s ? s.name : ds}}</span>`; }}
      const gs = _settings.notify_script || '';
      if (gs) {{ const s = _scripts.find(x => x.entity_id === gs); return `<span class="muted" style="font-size:.8rem">${{s ? s.name : gs}}</span>`; }}
      return '';
    }})();
    return `<tr class="device-row" data-eid="${{d.entity_id}}"${{dim}}>
      <td>${{d.name}}</td>
      <td>${{levelCell(d)}}</td>
      <td onclick="event.stopPropagation()">
        <select class="inline-select" data-eid="${{d.entity_id}}">
          ${{thrCell}}
        </select>
      </td>
      <td onclick="event.stopPropagation()">
        <label class="notify-check"><input type="checkbox" data-eid="${{d.entity_id}}" data-field="notify_bell" ${{d.notify_bell !== false ? 'checked' : ''}}> UI</label>
        <label class="notify-check"><input type="checkbox" data-eid="${{d.entity_id}}" data-field="notify_email" ${{d.notify_email !== false ? 'checked' : ''}}> Email</label>
        <label class="notify-check"><input type="checkbox" data-eid="${{d.entity_id}}" data-field="notify_mobile" ${{d.notify_mobile ? 'checked' : ''}}> Mobile</label>
      </td>
      <td>${{scriptLabel}}</td>
      <td class="muted">${{d.area || ''}}</td>
    </tr>`;
  }}).join("");

  document.getElementById("content").innerHTML = `<table>
    <colgroup>
      <col data-col="name"   style="width:${{_colWidths.name}}%">
      <col data-col="level"  style="width:${{_colWidths.level}}%">
      <col data-col="alert"  style="width:${{_colWidths.alert}}%">
      <col data-col="notify" style="width:${{_colWidths.notify}}%">
      <col data-col="script" style="width:${{_colWidths.script}}%">
      <col data-col="area"   style="width:${{_colWidths.area}}%">
    </colgroup>
    <thead><tr>
      <th data-col="name"  class="${{thCls('name')}}">Device${{arrow('name')}}<span class="resize-handle" data-col="name"></span></th>
      <th data-col="level" class="${{thCls('level')}}">Level${{arrow('level')}}<span class="resize-handle" data-col="level"></span></th>
      <th>Alert Threshold<span class="resize-handle" data-col="alert"></span></th>
      <th onclick="event.stopPropagation()">
        <label class="notify-check"><input type="checkbox" id="hdr-notify_bell"> UI</label>
        <label class="notify-check"><input type="checkbox" id="hdr-notify_email"> Email</label>
        <label class="notify-check"><input type="checkbox" id="hdr-notify_mobile"> Mobile</label>
        <span class="resize-handle" data-col="notify"></span>
      </th>
      <th>Script<span class="resize-handle" data-col="script"></span></th>
      <th data-col="area"  class="${{thCls('area')}}">Room${{arrow('area')}}<span class="resize-handle" data-col="area"></span></th>
    </tr></thead>
    <tbody>${{rows}}</tbody>
  </table>`;

  document.querySelectorAll("th[data-col]").forEach(th => {{
    th.addEventListener("click", e => {{
      if (e.target.classList.contains("resize-handle")) return;
      if (_wasDragging) {{ _wasDragging = false; return; }}
      _sortCol === th.dataset.col ? (_sortDir *= -1) : (_sortCol = th.dataset.col, _sortDir = 1);
      renderTable();
    }});
  }});
  document.querySelectorAll(".device-row").forEach(row =>
    row.addEventListener("click", () => openModal(row.dataset.eid))
  );
  document.querySelectorAll(".inline-select").forEach(sel => {{
    sel.addEventListener("change", async e => {{
      const eid = sel.dataset.eid;
      const threshold = parseInt(sel.value, 10);
      const idx = _devices.findIndex(x => x.entity_id === eid);
      if (idx !== -1) _devices[idx].alert_threshold = threshold;
      try {{
        await fetch(BASE + "/api/device/" + encodeURIComponent(eid), {{
          method: "POST", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{alert_threshold: threshold}}),
        }});
      }} catch {{ /* non-critical, local state already updated */ }}
      const scrollY = window.scrollY;
      renderTable();
      window.scrollTo(0, scrollY);
    }});
  }});
  document.querySelectorAll(".resize-handle").forEach(h => {{
    h.addEventListener("click",     e => e.stopPropagation());
    h.addEventListener("mousedown", e => startResize(e, h.dataset.col));
  }});
  document.querySelectorAll('input[data-field^="notify_"]').forEach(cb => {{
    cb.addEventListener("change", async () => {{
      const eid   = cb.dataset.eid;
      const field = cb.dataset.field;
      const idx   = _devices.findIndex(x => x.entity_id === eid);
      if (idx !== -1) _devices[idx][field] = cb.checked;
      updateHeaderCheckboxes();
      try {{
        await fetch(BASE + "/api/device/" + encodeURIComponent(eid), {{
          method: "POST", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{[field]: cb.checked}}),
        }});
      }} catch {{ /* non-critical */ }}
    }});
  }});
  ["notify_bell", "notify_email", "notify_mobile"].forEach(field => {{
    const hdr = document.getElementById("hdr-" + field);
    if (hdr) hdr.addEventListener("change", () => toggleAll(field, hdr.checked));
  }});
  updateHeaderCheckboxes();
}}

// ── Modal ─────────────────────────────────────────────────────────────
function openModal(eid) {{
  const d = _devices.find(x => x.entity_id === eid);
  if (!d) return;
  _activeEntity = eid;

  document.getElementById("modal-name-input").value    = d.name;
  document.getElementById("modal-entity").textContent  = eid;
  document.getElementById("modal-notes").value         = d.notes || '';
  document.getElementById("modal-replaced").value      = d.last_replaced || '';

  const thr = d.alert_threshold !== undefined ? d.alert_threshold : (_settings.default_threshold || 15);
  const thrEl = document.getElementById("modal-threshold");
  if (eid.startsWith("binary_sensor.")) {{
    thrEl.innerHTML =
      `<option value="15"${{thr !== -1 ? ' selected' : ''}}>Monitor</option>` +
      `<option value="-1"${{thr === -1  ? ' selected' : ''}}>Ignore</option>`;
  }} else {{
    thrEl.innerHTML =
      [5,10,15,20,25,30].map(n => `<option value="${{n}}"${{thr===n?' selected':''}}>${{n}}%</option>`).join('') +
      `<option value="-1"${{thr===-1?' selected':''}}>Ignore</option>`;
  }}

  const types = _settings.battery_types || [];
  const typeEl = document.getElementById("modal-type");
  typeEl.innerHTML = '<option value="">-- Not set --</option>' +
    types.map(t => `<option value="${{t}}"${{d.battery_type === t ? ' selected' : ''}}>${{t}}</option>`).join('');
  if (d.battery_type && !types.includes(d.battery_type)) {{
    typeEl.innerHTML += `<option value="${{d.battery_type}}" selected>${{d.battery_type}} (custom)</option>`;
  }}

  document.getElementById("modal-notify-bell").checked   = d.notify_bell  !== false;
  document.getElementById("modal-notify-email").checked  = d.notify_email !== false;
  document.getElementById("modal-notify-mobile").checked = !!d.notify_mobile;
  document.getElementById("modal-notify-addr").value    = d.notify_email_address || '';

  const mobileEl  = document.getElementById("modal-mobile-service");
  const mobileSvcs = _notifyServices.filter(s => s.startsWith("mobile_app_"));
  mobileEl.innerHTML = '<option value="">-- Disabled --</option>' +
    mobileSvcs.map(s => `<option value="${{s}}"${{d.notify_mobile_service === s ? ' selected' : ''}}>${{s.replace('mobile_app_', '')}}</option>`).join('');
  if (d.notify_mobile_service && !mobileSvcs.includes(d.notify_mobile_service)) {{
    mobileEl.innerHTML += `<option value="${{d.notify_mobile_service}}" selected>${{d.notify_mobile_service}} (not found)</option>`;
  }}

  const scriptEl = document.getElementById("modal-notify-script");
  scriptEl.innerHTML =
    '<option value="">-- Use global default --</option>' +
    '<option value="__disabled__">-- Disabled for this device --</option>' +
    _scripts.map(s => `<option value="${{s.entity_id}}"${{d.notify_script === s.entity_id ? ' selected' : ''}}>${{s.name}}</option>`).join('');
  if (d.notify_script === '__disabled__') scriptEl.value = '__disabled__';
  else if (!d.notify_script) scriptEl.value = '';

  document.getElementById("modal").classList.add("open");
}}

function closeModal() {{
  document.getElementById("modal").classList.remove("open");
  _activeEntity = null;
  cancelDelete();
}}

function confirmDelete() {{
  document.getElementById("modal-footer-normal").style.display = "none";
  document.getElementById("modal-footer-confirm").style.display = "flex";
}}

function cancelDelete() {{
  document.getElementById("modal-footer-normal").style.display = "flex";
  document.getElementById("modal-footer-confirm").style.display = "none";
}}

async function deleteDevice() {{
  if (!_activeEntity) return;
  try {{
    const r = await fetch(BASE + "/api/device/" + encodeURIComponent(_activeEntity), {{
      method: "DELETE",
    }});
    if (!r.ok) throw new Error();
    _devices = _devices.filter(d => d.entity_id !== _activeEntity);
    closeModal();
    if (_devices.length) renderTable();
    else document.getElementById("content").innerHTML = '<p class="loading">No battery devices found.</p>';
  }} catch {{
    alert("Failed to delete device.");
  }}
}}

document.getElementById("modal").addEventListener("click", e => {{
  if (e.target.id === "modal") closeModal();
}});

function markReplacedToday() {{
  document.getElementById("modal-replaced").value = new Date().toISOString().split('T')[0];
}}

async function saveDevice() {{
  if (!_activeEntity) return;
  const d = _devices.find(x => x.entity_id === _activeEntity);
  const newName = document.getElementById("modal-name-input").value.trim();
  const nameChanged = newName && d && newName !== d.name;
  const payload = {{
    notes:                 document.getElementById("modal-notes").value,
    battery_type:          document.getElementById("modal-type").value,
    alert_threshold:       parseInt(document.getElementById("modal-threshold").value, 10),
    last_replaced:         document.getElementById("modal-replaced").value || null,
    notify_bell:           document.getElementById("modal-notify-bell").checked,
    notify_email:          document.getElementById("modal-notify-email").checked,
    notify_mobile:         document.getElementById("modal-notify-mobile").checked,
    notify_email_address:  document.getElementById("modal-notify-addr").value.trim(),
    notify_mobile_service: document.getElementById("modal-mobile-service").value,
    notify_script:         document.getElementById("modal-notify-script").value,
  }};
  try {{
    const r = await fetch(BASE + "/api/device/" + encodeURIComponent(_activeEntity), {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }});
    if (!r.ok) throw new Error();
    const updated = await r.json();
    const idx = _devices.findIndex(x => x.entity_id === _activeEntity);
    if (idx !== -1) _devices[idx] = {{ ..._devices[idx], ...updated }};

    if (nameChanged) {{
      const rr = await fetch(BASE + "/api/rename/" + encodeURIComponent(_activeEntity), {{
        method: "POST", headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{name: newName}}),
      }});
      if (rr.ok) {{
        const idx2 = _devices.findIndex(x => x.entity_id === _activeEntity);
        if (idx2 !== -1) _devices[idx2].name = newName;
      }} else {{
        alert("Device settings saved, but renaming in Home Assistant failed.");
      }}
    }}

    closeModal();
    renderTable();
  }} catch {{
    alert("Failed to save device settings.");
  }}
}}

// ── Notify header helpers ─────────────────────────────────────────────
function updateHeaderCheckboxes() {{
  const checks = {{
    notify_bell:   d => d.notify_bell  !== false,
    notify_email:  d => d.notify_email !== false,
    notify_mobile: d => !!d.notify_mobile,
  }};
  Object.entries(checks).forEach(([field, fn]) => {{
    const hdr = document.getElementById('hdr-' + field);
    if (!hdr || !_devices.length) return;
    const n = _devices.filter(fn).length;
    hdr.checked       = n === _devices.length;
    hdr.indeterminate = n > 0 && n < _devices.length;
  }});
}}

async function toggleAll(field, checked) {{
  _devices.forEach(d => d[field] = checked);
  await Promise.allSettled(_devices.map(d =>
    fetch(BASE + "/api/device/" + encodeURIComponent(d.entity_id), {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{[field]: checked}}),
    }})
  ));
  renderTable();
}}

// ── Scan ──────────────────────────────────────────────────────────────
async function scanNow() {{
  const status = document.getElementById("scan-status");
  status.textContent = "Scanning...";
  try {{
    await fetch(BASE + "/api/scan", {{ method: "POST" }});
    await new Promise(r => setTimeout(r, 1500));
    const r = await fetch(BASE + "/api/batteries");
    _devices = await r.json();
    renderTable();
    status.textContent = "Done.";
  }} catch {{
    status.textContent = "Scan failed.";
  }}
  setTimeout(() => status.textContent = '', 3000);
}}

// ── Settings ──────────────────────────────────────────────────────────
function renderTypeList() {{
  document.getElementById("type-list").innerHTML =
    (_settings.battery_types || []).map((t, i) =>
      `<li class="type-item">
        <span>${{t}}</span>
        <button class="type-delete" onclick="deleteType(${{i}})" title="Remove">&times;</button>
      </li>`
    ).join('');
}}

function deleteType(idx) {{
  _settings.battery_types.splice(idx, 1);
  renderTypeList();
}}

function addType() {{
  const inp = document.getElementById("type-input");
  const val = inp.value.trim();
  if (!val || (_settings.battery_types || []).includes(val)) {{ inp.value = ''; return; }}
  _settings.battery_types.push(val);
  renderTypeList();
  inp.value = '';
}}

document.getElementById("type-input").addEventListener("keydown", e => {{
  if (e.key === "Enter") addType();
}});

async function saveSettings() {{
  _settings.default_threshold      = parseInt(document.getElementById("setting-threshold").value, 10);
  _settings.notify_persistent      = document.getElementById("setting-notify-persistent").checked;
  _settings.notify_new_device      = document.getElementById("setting-notify-new-device").checked;
  _settings.check_interval         = Math.max(1, parseInt(document.getElementById("setting-check-interval").value, 10) || 10);
  _settings.notify_email_service          = document.getElementById("setting-notify-service").value;
  _settings.notify_mobile_default_service = document.getElementById("setting-mobile-default").value;
  _settings.notify_script                 = document.getElementById("setting-notify-script").value;
  _settings.notify_email_to        = document.getElementById("setting-notify-to").value.trim();
  _settings.notify_email_cc        = document.getElementById("setting-notify-cc").value.trim();
  _settings.daily_report_enabled   = document.getElementById("setting-daily-enabled").checked;
  _settings.daily_report_time      = document.getElementById("setting-daily-time").value;
  _settings.daily_report_include_all    = document.getElementById("setting-daily-include").value === 'all';
  _settings.report_include_battery_type = document.getElementById("setting-include-battery-type").checked;
  try {{
    const r = await fetch(BASE + "/api/settings", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(_settings),
    }});
    if (!r.ok) throw new Error();
    const status = document.getElementById("settings-status");
    status.textContent = "Saved.";
    setTimeout(() => status.textContent = '', 2500);
  }} catch {{
    alert("Failed to save settings.");
  }}
}}

// ── Init ──────────────────────────────────────────────────────────────
async function init() {{
  try {{
    const [devRes, setRes, svcRes, scriptRes] = await Promise.all([
      fetch(BASE + "/api/batteries"),
      fetch(BASE + "/api/settings"),
      fetch(BASE + "/api/notify-services"),
      fetch(BASE + "/api/scripts"),
    ]);
    _devices        = await devRes.json();
    _settings       = await setRes.json();
    _notifyServices = await svcRes.json();
    _scripts        = await scriptRes.json();

    if (!_devices.length) {{
      document.getElementById("content").innerHTML = '<p class="loading">No battery devices found.</p>';
    }} else {{
      renderTable();
    }}

    document.getElementById("setting-threshold").value         = _settings.default_threshold || 15;
    document.getElementById("setting-notify-persistent").checked = _settings.notify_persistent !== false;
    document.getElementById("setting-notify-new-device").checked = _settings.notify_new_device !== false;
    document.getElementById("setting-check-interval").value     = _settings.check_interval || 10;
    document.getElementById("setting-notify-to").value         = _settings.notify_email_to || '';
    document.getElementById("setting-notify-cc").value         = _settings.notify_email_cc || '';
    document.getElementById("setting-daily-enabled").checked   = !!_settings.daily_report_enabled;
    document.getElementById("setting-daily-time").value        = _settings.daily_report_time || '08:00';
    document.getElementById("setting-daily-include").value          = _settings.daily_report_include_all ? 'all' : 'low';
    document.getElementById("setting-include-battery-type").checked = !!_settings.report_include_battery_type;

    const svcEl = document.getElementById("setting-notify-service");
    svcEl.innerHTML = '<option value="">-- None --</option>' +
      _notifyServices.map(s => `<option value="${{s}}"${{_settings.notify_email_service === s ? ' selected' : ''}}>${{s}}</option>`).join('');

    const mobileSvcs = _notifyServices.filter(s => s.startsWith("mobile_app_"));
    const mobEl = document.getElementById("setting-mobile-default");
    mobEl.innerHTML = '<option value="">-- None --</option>' +
      mobileSvcs.map(s => `<option value="${{s}}"${{_settings.notify_mobile_default_service === s ? ' selected' : ''}}>${{s.replace('mobile_app_', '')}}</option>`).join('');

    const scriptSettingEl = document.getElementById("setting-notify-script");
    scriptSettingEl.innerHTML = '<option value="">-- None --</option>' +
      _scripts.map(s => `<option value="${{s.entity_id}}"${{_settings.notify_script === s.entity_id ? ' selected' : ''}}>${{s.name}}</option>`).join('');

    renderTypeList();
    document.getElementById("device-search").addEventListener("input", renderTable);
  }} catch(e) {{
    document.getElementById("content").innerHTML = '<p class="error">Failed to load battery data.</p>';
  }}
}}
init();
</script>
</body>
</html>"""


async def on_startup(app):
    asyncio.ensure_future(refresh_loop())


def main():
    app = web.Application()
    app.on_startup.append(on_startup)
    app.router.add_get("/",                          handle_index)
    app.router.add_get("/api/batteries",             handle_api_batteries)
    app.router.add_get("/api/settings",              handle_api_settings_get)
    app.router.add_post("/api/settings",             handle_api_settings_post)
    app.router.add_post("/api/scan",                 handle_api_scan)
    app.router.add_get("/api/notify-services",       handle_api_notify_services)
    app.router.add_get("/api/scripts",               handle_api_scripts)
    app.router.add_post("/api/device/{entity_id}",     handle_api_device_post)
    app.router.add_delete("/api/device/{entity_id}",   handle_api_device_delete)
    app.router.add_post("/api/rename/{entity_id}",     handle_api_rename)

    port = int(os.environ.get("INGRESS_PORT", 8099))
    _LOGGER.info("Starting Battery Sentinel on port %d", port)
    web.run_app(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
