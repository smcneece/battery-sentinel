"""Z-Wave node health monitoring -- detects dead nodes and fires alerts.

Queries all sensor.*_node_status entities from HA. Tracks dead_since and
alert_sent per node in storage._zwave_nodes. Fires notifications after a
configurable delay, using the same pattern as the unavailable device monitor.

Per-node channel settings (notify_bell, notify_email, notify_mobile) and
muted_until are respected -- all unchecked = no alerts for that node.

Bulk dead detection: if 80%+ of nodes go dead simultaneously (e.g. during a
Z-Wave JS update or controller restart), individual per-node alerts are
suppressed. Instead a single "network disruption" alert fires after the
configured delay, followed by a single recovery alert. This prevents a flood
of notifications during routine Z-Wave service maintenance.

Only loaded when zwave_monitor_enabled is True in settings."""

import datetime
import logging
import re

import aiohttp

from ha_config import HA_API_URL, _headers
import storage
import notifications

_LOGGER = logging.getLogger(__name__)

_NAME_CLEANUP = re.compile(r'\s+node\s+status\s*$', re.IGNORECASE)

# If this fraction of nodes are simultaneously dead, treat as a controller/service event
_BULK_DEAD_THRESHOLD = 0.8

# Module-level state for controller health -- in-memory is fine; if Battery Sentinel
# restarts during a Z-Wave outage the add-on startup suppression handles first-scan noise
_controller_suspect_since: datetime.datetime | None = None
_controller_alert_sent: bool = False


async def get_node_statuses() -> list:
    """Fetch all Z-Wave node status sensors from HA.
    Filters for sensor.*_node_status entities -- the pattern used by Z-Wave JS.
    Returns list of {entity_id, name, state}."""
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
        return [
            {
                "entity_id": s["entity_id"],
                "name": _NAME_CLEANUP.sub(
                    "", s["attributes"].get("friendly_name", s["entity_id"])
                ).strip(),
                "state": s["state"],
            }
            for s in states
            if s["entity_id"].startswith("sensor.") and s["entity_id"].endswith("_node_status")
        ]
    except Exception:
        _LOGGER.exception("Failed to fetch Z-Wave node statuses")
        return []


async def check_nodes(settings: dict, first_run: bool, metadata: dict = None):
    """Check all Z-Wave node statuses and fire alerts as needed.

    Called from do_refresh() in main.py when zwave_monitor_enabled is True.
    metadata (entity_id -> {area, ...}) is passed from main.py so area info
    is available without an extra API call.

    Bulk dead mode: if 80%+ of nodes are simultaneously dead, suppress
    individual alerts and fire one consolidated controller alert instead.
    Per-node alerts resume for any nodes still dead after the bulk event clears."""
    global _controller_suspect_since, _controller_alert_sent

    nodes = await get_node_statuses()
    if not nodes:
        return

    if metadata:
        for node in nodes:
            node["area"] = metadata.get(node["entity_id"], {}).get("area", "")

    delay_seconds = int(settings.get("zwave_alert_delay", 5)) * 60
    now = datetime.datetime.now()

    merged = storage.merge_zwave_nodes(nodes)
    stored = {n["entity_id"]: n for n in merged}

    total = len(nodes)
    dead_count = sum(1 for n in nodes if n["state"] == "dead")
    bulk_dead = total >= 2 and dead_count / total >= _BULK_DEAD_THRESHOLD

    if bulk_dead:
        if _controller_suspect_since is None:
            _controller_suspect_since = now
            _LOGGER.info("Z-Wave bulk dead: %d/%d nodes offline, suspecting controller/service issue", dead_count, total)

        if not _controller_alert_sent and not first_run:
            if (now - _controller_suspect_since).total_seconds() >= delay_seconds:
                _controller_alert_sent = True
                _LOGGER.warning("Z-Wave controller alert: %d/%d nodes dead for >%d min", dead_count, total, delay_seconds // 60)
                await notifications.fire_zwave_controller_alert(dead_count, total, settings)

        # Still record dead_since per node so we know when each went down,
        # but don't fire individual alerts while in bulk dead mode
        for node in nodes:
            eid = node["entity_id"]
            if node["state"] == "dead" and not stored.get(eid, {}).get("dead_since"):
                storage.update_zwave_node(eid, {"dead_since": now.isoformat(), "alert_sent": False})
        return

    # ── Bulk dead condition has cleared ──────────────────────────────────
    if _controller_suspect_since is not None:
        was_alerted = _controller_alert_sent
        _controller_suspect_since = None
        _controller_alert_sent = False
        if was_alerted and not first_run:
            alive_count = sum(1 for n in nodes if n["state"] != "dead")
            _LOGGER.info("Z-Wave controller recovered: %d/%d nodes back online", alive_count, total)
            await notifications.fire_zwave_controller_recovered(alive_count, total, settings)
        # Clear dead_since for nodes that recovered during the bulk event so they
        # don't immediately re-fire as individuals on the next cycle
        for node in nodes:
            eid = node["entity_id"]
            if node["state"] != "dead" and stored.get(eid, {}).get("dead_since"):
                storage.update_zwave_node(eid, {"dead_since": None, "alert_sent": False})
        # Nodes still dead after bulk clears fall through to normal per-node processing below

    # ── Normal per-node processing ────────────────────────────────────────
    for node in nodes:
        eid = node["entity_id"]
        is_dead = node["state"] == "dead"
        entry = stored.get(eid, {})

        is_muted = False
        muted_until = entry.get("muted_until")
        if muted_until:
            try:
                mu_dt = datetime.datetime.fromisoformat(muted_until)
                now_cmp = datetime.datetime.now(datetime.timezone.utc) if mu_dt.tzinfo else now
                if now_cmp < mu_dt:
                    is_muted = True
                else:
                    storage.update_zwave_node(eid, {"muted_until": None})
            except Exception:
                pass

        if is_dead:
            if not entry.get("dead_since"):
                storage.update_zwave_node(eid, {
                    "dead_since": now.isoformat(),
                    "alert_sent": False,
                })
                entry = storage.get_zwave_nodes().get(eid, entry)

            if not entry.get("alert_sent") and not first_run and not is_muted:
                try:
                    since = datetime.datetime.fromisoformat(entry["dead_since"])
                    if (now - since).total_seconds() >= delay_seconds:
                        storage.update_zwave_node(eid, {"alert_sent": True})
                        node_with_settings = {**entry, "state": node["state"]}
                        _LOGGER.info("Z-Wave dead node alert: %s", eid)
                        await notifications.fire_zwave_node_dead(node_with_settings, settings)
                        script = (entry.get("notify_script") or "").strip() or settings.get("zwave_notify_script", "").strip()
                        if script and script != "__disabled__":
                            await notifications.fire_zwave_script(script, node)
                except Exception:
                    _LOGGER.exception("Z-Wave dead alert failed for %s", eid)

        else:
            if entry.get("dead_since"):
                was_alerted = entry.get("alert_sent", False)
                storage.update_zwave_node(eid, {"dead_since": None, "alert_sent": False})
                if was_alerted and not is_muted:
                    node_with_settings = {**entry, "state": node["state"]}
                    _LOGGER.info("Z-Wave node recovery: %s", eid)
                    await notifications.fire_zwave_node_recovered(node_with_settings, settings)
