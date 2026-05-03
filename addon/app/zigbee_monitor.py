"""Zigbee device offline monitoring -- detects devices that have stopped reporting.

Queries all sensor.*_last_seen entities (device_class: timestamp) created by
Zigbee2MQTT when last_seen is enabled. Tracks offline_since and alert_sent per
node in storage._zigbee_nodes. Fires notifications after a configurable delay.

A device is considered offline when its last_seen timestamp is older than
zigbee_offline_threshold hours. The alert fires after notify_unavailable_delay minutes
of being over threshold, matching the same pattern as unavailable device alerts.

Per-node channel settings (notify_bell, notify_email, notify_mobile) and
muted_until are respected.

Only active when zigbee_monitor_enabled is True in settings."""

import datetime
import logging

import ha_api
import storage
import notifications

_LOGGER = logging.getLogger(__name__)


async def check_nodes(settings: dict, first_run: bool):
    """Check all Zigbee last_seen timestamps and fire alerts as needed."""
    nodes = await ha_api.get_zigbee_last_seen_entities()
    if not nodes:
        _LOGGER.debug("No Zigbee last_seen entities found")
        return

    threshold_hours = float(settings.get("zigbee_offline_threshold", 24))
    delay_seconds = int(settings.get("notify_unavailable_delay", 5)) * 60
    now = datetime.datetime.now(datetime.timezone.utc)

    merged = storage.merge_zigbee_nodes(nodes)
    stored = {n["entity_id"]: n for n in merged}

    for node in nodes:
        eid = node["entity_id"]
        entry = stored.get(eid, {})
        state = node["state"]

        is_muted = False
        muted_until = entry.get("muted_until")
        if muted_until:
            try:
                mu_dt = datetime.datetime.fromisoformat(muted_until)
                now_cmp = datetime.datetime.now(datetime.timezone.utc) if mu_dt.tzinfo else datetime.datetime.now()
                if now_cmp < mu_dt:
                    is_muted = True
                else:
                    storage.update_zigbee_node(eid, {"muted_until": None})
            except Exception:
                pass

        is_offline = _is_offline(state, threshold_hours, now)

        if is_offline:
            if not entry.get("offline_since"):
                storage.update_zigbee_node(eid, {
                    "offline_since": datetime.datetime.now().isoformat(),
                    "alert_sent": False,
                })
                entry = storage.get_zigbee_nodes().get(eid, entry)

            if not entry.get("alert_sent") and not first_run and not is_muted:
                try:
                    since = datetime.datetime.fromisoformat(entry["offline_since"])
                    if (datetime.datetime.now() - since).total_seconds() >= delay_seconds:
                        storage.update_zigbee_node(eid, {"alert_sent": True})
                        node_with_settings = {**entry, "state": state}
                        _LOGGER.info("Zigbee offline alert: %s (last seen: %s)", eid, state)
                        await notifications.fire_zigbee_node_offline(node_with_settings, settings)
                        script = (entry.get("notify_script") or "").strip() or settings.get("notify_script", "").strip()
                        if script and script != "__disabled__":
                            await notifications.fire_script(script, {
                                "device_name": node.get("name", ""),
                                "entity_id":   node["entity_id"],
                                "last_seen":   node.get("state", ""),
                                "device_type": "zigbee",
                            })
                except Exception:
                    _LOGGER.exception("Zigbee offline alert failed for %s", eid)

        else:
            if entry.get("offline_since"):
                was_alerted = entry.get("alert_sent", False)
                storage.update_zigbee_node(eid, {"offline_since": None, "alert_sent": False})
                if was_alerted and not is_muted:
                    node_with_settings = {**entry, "state": state}
                    _LOGGER.info("Zigbee node recovery: %s", eid)
                    await notifications.fire_zigbee_node_recovered(node_with_settings, settings)


def _is_offline(state: str, threshold_hours: float, now: datetime.datetime) -> bool:
    """Return True if the last_seen state indicates the device is offline."""
    if state in ("unavailable", "unknown", ""):
        return True
    try:
        last_seen = datetime.datetime.fromisoformat(state.replace("Z", "+00:00"))
        if last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=datetime.timezone.utc)
        age_hours = (now - last_seen).total_seconds() / 3600
        return age_hours > threshold_hours
    except Exception:
        return False
