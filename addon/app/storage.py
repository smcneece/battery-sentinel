import json
import logging
import os

_LOGGER = logging.getLogger(__name__)

DATA_FILE = "/data/battery_sentinel.json"

DEFAULT_BATTERY_TYPES = ["AA", "AAA", "C", "9V", "CR2032", "CR2025", "CR123A", "CR2", "18650", "Rechargeable"]

DEFAULT_SETTINGS = {
    "default_threshold": 15,
    "battery_types": DEFAULT_BATTERY_TYPES[:],
    "notify_persistent": True,
    "notify_email_service": "",
    "notify_email_to": "",
    "notify_email_cc": "",
    "notify_mobile_default_service": "",
    "notify_script": "",
    "notify_new_device": True,
    "check_interval": 10,
    "daily_report_enabled": False,
    "daily_report_time": "08:00",
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
        "daily_report_enabled", "daily_report_time", "daily_report_include_all",
        "daily_report_send_if_ok", "report_include_battery_type",
    )
    for key in allowed:
        if key in updates:
            current[key] = updates[key]
    data["settings"] = current
    _save(data)
    return current


def merge_entities(live_entities: list) -> tuple[list, list]:
    """Returns (new_entity_ids, sorted_device_list)."""
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
                "last_replaced": None,
                "notify_bell": True,
                "notify_email": True,
                "notify_mobile": False,
                "notify_email_address": "",
                "notify_mobile_service": "",
                "notify_script": "",
                "script_last_run": None,
            }
        else:
            devices[eid]["name"] = entity["name"]
            devices[eid].setdefault("alert_threshold", default_threshold)
            devices[eid].setdefault("alert_sent", False)
            devices[eid].setdefault("notify_bell", True)
            devices[eid].setdefault("notify_email", True)
            devices[eid].setdefault("notify_mobile", False)
            devices[eid].setdefault("notify_email_address", "")
            devices[eid].setdefault("notify_mobile_service", "")
            devices[eid].setdefault("notify_script", "")
            devices[eid].setdefault("script_last_run", None)

    _save(data)
    _LOGGER.info("Devices: %d total, %d new", len(live_entities), len(new_eids))

    result = []
    for entity in live_entities:
        eid = entity["entity_id"]
        result.append({
            **devices[eid],
            "state": entity["state"],
            "area": entity.get("area", ""),
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
    }
    for key, val in fields.items():
        if key in allowed:
            devices[entity_id][key] = val
    _save(data)
    return devices[entity_id]


def delete_device(entity_id: str):
    data = _load()
    data.get("devices", {}).pop(entity_id, None)
    _save(data)


def set_alert_sent(entity_id: str, sent: bool):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["alert_sent"] = sent
        _save(data)


def set_script_last_run(entity_id: str, date_str: str):
    data = _load()
    if entity_id in data.get("devices", {}):
        data["devices"][entity_id]["script_last_run"] = date_str
        _save(data)


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
