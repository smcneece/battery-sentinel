"""JSON persistence layer for Battery Sentinel.

All state lives in a single file at DATA_FILE with three top-level keys:
  devices  -- per-device metadata keyed by entity_id
  settings -- user-configured global settings
  _state   -- internal app state (e.g. last daily report date)

Every public function does a full load-modify-save cycle. This is safe because
the add-on is single-process and HA add-ons don't have concurrent writers."""

import json
import logging
import os

_LOGGER = logging.getLogger(__name__)

DATA_FILE = "/data/battery_sentinel.json"

DEFAULT_BATTERY_TYPES = ["AA", "AAA", "C", "9V", "CR2032", "CR2025", "CR123A", "CR2", "18650", "Rechargeable"]

DEFAULT_SETTINGS = {
    "default_threshold": 20,
    "battery_types": DEFAULT_BATTERY_TYPES[:],
    "notify_persistent": True,
    "notify_email_service": "",
    "notify_email_to": "",
    "notify_email_cc": "",
    "notify_mobile_default_service": "",
    "notify_script": "",
    "notify_new_device": True,
    "notify_unavailable": False,
    "notify_unavailable_delay": 5,
    "zwave_monitor_enabled": False,
    "zwave_alert_delay": 5,
    "zwave_notify_bell": True,
    "zwave_notify_email": True,
    "zwave_notify_mobile": False,
    "zwave_notify_script": "",
    "check_interval": 10,
    "daily_report_enabled": False,
    "daily_report_time": "08:00",
    "daily_report_days": [0, 1, 2, 3, 4, 5, 6],
    "daily_report_include_all": False,
    "daily_report_send_if_ok": False,
    "report_include_battery_type": False,
}


def _load() -> dict:
    if not os.path.exists(DATA_FILE):
        return {"devices": {}, "settings": {}, "_state": {}}
    try:
        with open(DATA_FILE) as f:
            data = json.load(f)
        data.setdefault("devices", {})
        data.setdefault("settings", {})
        data.setdefault("_state", {})
        return data
    except Exception:
        _LOGGER.exception("Failed to load data file, starting fresh")
        return {"devices": {}, "settings": {}, "_state": {}}


def _save(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        _LOGGER.exception("Failed to save data file")


def get_settings() -> dict:
    stored = _load().get("settings", {})
    return {**DEFAULT_SETTINGS, **stored}


def save_settings(updates: dict) -> dict:
    data = _load()
    current = {**DEFAULT_SETTINGS, **data.get("settings", {})}
    allowed = (
        "default_threshold", "battery_types",
        "notify_persistent", "notify_email_service",
        "notify_email_to", "notify_email_cc",
        "notify_mobile_default_service", "notify_script",
        "notify_new_device", "check_interval",
        "daily_report_enabled", "daily_report_time", "daily_report_days",
        "daily_report_include_all", "daily_report_send_if_ok", "report_include_battery_type",
        "notify_unavailable", "notify_unavailable_delay",
        "zwave_monitor_enabled", "zwave_alert_delay",
        "zwave_notify_bell", "zwave_notify_email", "zwave_notify_mobile", "zwave_notify_script",
    )
    for key in allowed:
        if key in updates:
            current[key] = updates[key]
    data["settings"] = current
    _save(data)
    return current


def merge_entities(live_entities: list) -> tuple[list, list]:
    """Merge live HA entities into stored device records.

    New entities get default metadata. Existing records keep all user-set fields
    and only gain any new keys added since they were first seen (via setdefault).
    Returns (new_entity_ids, sorted_device_list) -- hidden devices are excluded from the list."""
    data = _load()
    devices = data.setdefault("devices", {})
    default_threshold = {**DEFAULT_SETTINGS, **data.get("settings", {})}.get("default_threshold", 15)

    new_eids = []
    for entity in live_entities:
        eid = entity["entity_id"]
        if eid not in devices:
            new_eids.append(eid)
            devices[eid] = {
                "entity_id": eid,
                "name": entity["name"],
                "notes": "",
                "battery_type": "",
                "alert_threshold": default_threshold,
                "alert_sent": False,
                "unavailable_sent": False,
                "unavailable_since": None,
                "hidden": False,
                "last_replaced": None,
                "notify_bell": True,
                "notify_email": True,
                "notify_mobile": False,
                "notify_email_address": "",
                "notify_mobile_service": "",
                "notify_script": "",
                "script_last_run": None,
                "muted_until": None,
            }
        else:
            devices[eid]["name"] = entity["name"]
            devices[eid].setdefault("alert_threshold", default_threshold)
            devices[eid].setdefault("alert_sent", False)
            devices[eid].setdefault("unavailable_sent", False)
            devices[eid].setdefault("unavailable_since", None)
            devices[eid].setdefault("hidden", False)
            devices[eid].setdefault("notify_bell", True)
            devices[eid].setdefault("notify_email", True)
            devices[eid].setdefault("notify_mobile", False)
            devices[eid].setdefault("notify_email_address", "")
            devices[eid].setdefault("notify_mobile_service", "")
            devices[eid].setdefault("notify_script", "")
            devices[eid].setdefault("script_last_run", None)
            devices[eid].setdefault("muted_until", None)

    _save(data)
    _LOGGER.info("Devices: %d total, %d new", len(live_entities), len(new_eids))

    result = []
    for entity in live_entities:
        eid = entity["entity_id"]
        if devices[eid].get("hidden"):
            continue
        result.append({
            **devices[eid],
            "state":        entity["state"],
            "area":         entity.get("area", ""),
            "device_id":    entity.get("device_id", ""),
            "manufacturer": entity.get("manufacturer", ""),
            "model":        entity.get("model", ""),
        })

    return new_eids, sorted(result, key=_sort_key)


def save_device(entity_id: str, fields: dict) -> dict:
    data = _load()
    devices = data.setdefault("devices", {})
    if entity_id not in devices:
        raise KeyError(f"Device {entity_id} not found")
    allowed = {
        "notes", "battery_type", "alert_threshold", "last_replaced",
        "notify_bell", "notify_email", "notify_mobile",
        "notify_email_address", "notify_mobile_service", "notify_script",
        "muted_until",
    }
    for key, val in fields.items():
        if key in allowed:
            devices[entity_id][key] = val
    _save(data)
    return devices[entity_id]


def delete_device(entity_id: str):
    """Soft-delete: marks hidden so it's excluded from the UI but metadata is preserved.
    If the entity reappears in HA it will be restored with all prior settings intact."""
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["hidden"] = True
        _save(data)


def restore_device(entity_id: str):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["hidden"] = False
        _save(data)


def purge_device(entity_id: str):
    """Hard-delete: removes the record entirely. Use when you want a clean slate for an entity."""
    data = _load()
    data.get("devices", {}).pop(entity_id, None)
    _save(data)


def get_hidden_devices() -> list:
    data = _load()
    return [d for d in data.get("devices", {}).values() if d.get("hidden")]


def set_alert_sent(entity_id: str, sent: bool):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["alert_sent"] = sent
        _save(data)


def set_unavailable_sent(entity_id: str, sent: bool):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["unavailable_sent"] = sent
        _save(data)


def set_unavailable_since(entity_id: str, value):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["unavailable_since"] = value
        _save(data)


def set_script_last_run(entity_id: str, date_str: str):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["script_last_run"] = date_str
        _save(data)


def get_zwave_nodes() -> dict:
    """Return the full _zwave_nodes tracking dict, keyed by entity_id."""
    return _load().get("_zwave_nodes", {})


def update_zwave_node(entity_id: str, fields: dict):
    """Create or update a Z-Wave node tracking entry."""
    data = _load()
    nodes = data.setdefault("_zwave_nodes", {})
    if entity_id not in nodes:
        nodes[entity_id] = {}
    nodes[entity_id].update(fields)
    _save(data)


def merge_zwave_nodes(live_nodes: list) -> list:
    """Ensure all discovered Z-Wave nodes have storage entries with defaults.

    New nodes get default notification settings. Existing nodes keep user
    settings but have their live state (state, area, name) updated.
    Returns list of merged node dicts (stored settings + live state)."""
    data = _load()
    nodes = data.setdefault("_zwave_nodes", {})

    for node in live_nodes:
        eid = node["entity_id"]
        if eid not in nodes:
            nodes[eid] = {
                "entity_id":            eid,
                "name":                 node["name"],
                "notes":                "",
                "notify_bell":          True,
                "notify_email":         True,
                "notify_mobile":        False,
                "notify_email_address": "",
                "notify_script":        "",
                "muted_until":          None,
                "dead_since":           None,
                "alert_sent":           False,
            }
        else:
            nodes[eid].setdefault("notes", "")
            nodes[eid].setdefault("notify_bell", True)
            nodes[eid].setdefault("notify_email", True)
            nodes[eid].setdefault("notify_mobile", False)
            nodes[eid].setdefault("notify_email_address", "")
            nodes[eid].setdefault("notify_script", "")
            nodes[eid].setdefault("muted_until", None)
        nodes[eid]["entity_id"] = eid
        nodes[eid]["name"]      = node["name"]
        nodes[eid]["state"]     = node["state"]
        nodes[eid]["area"]      = node.get("area", nodes[eid].get("area", ""))

    live_eids = {n["entity_id"] for n in live_nodes}
    stale = [k for k in nodes if k not in live_eids]
    for k in stale:
        del nodes[k]

    _save(data)
    return [nodes[n["entity_id"]] for n in live_nodes]


def save_zwave_node(entity_id: str, fields: dict) -> dict:
    """Update user-editable settings for a Z-Wave node."""
    data = _load()
    nodes = data.setdefault("_zwave_nodes", {})
    if entity_id not in nodes:
        raise KeyError(f"Z-Wave node {entity_id} not found")
    allowed = {
        "notes", "notify_bell", "notify_email", "notify_mobile",
        "notify_email_address", "notify_script", "muted_until",
    }
    for key, val in fields.items():
        if key in allowed:
            nodes[entity_id][key] = val
    _save(data)
    return nodes[entity_id]


def get_last_report_date() -> str:
    return _load().get("_state", {}).get("last_report_date", "")


def set_last_report_date(date_str: str):
    data = _load()
    data.setdefault("_state", {})["last_report_date"] = date_str
    _save(data)


def _sort_key(d):
    if d["entity_id"].startswith("binary_sensor."):
        return 0 if d["state"] == "on" else 100
    try:
        return float(d["state"])
    except (ValueError, TypeError):
        return 999
