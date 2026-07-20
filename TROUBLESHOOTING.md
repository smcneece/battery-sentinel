# Battery Sentinel Plus: Troubleshooting

Before opening an issue, check below to see if your problem is already documented.

---

## No batteries appear in the device list

### 1. Check that HA has battery entities

Battery Sentinel Plus auto-discovers any entity with `device_class: battery`. If HA is not exposing your devices that way, nothing will appear.

Go to **Developer Tools > States** and filter for `battery`. Look for entities starting with `sensor.` or `binary_sensor.` that have a numeric value or `on`/`off`. If none appear, your devices may not be reporting battery level to HA at all -- this is usually an integration or firmware issue, not a Battery Sentinel Plus issue.

### 2. Check if the entity is hidden in HA

Entities can appear in Developer Tools > States but still be flagged as hidden in the entity registry. Battery Sentinel Plus skips hidden entities by design (so you can intentionally exclude things like browser battery sensors). Go to **Settings > Devices & Services > Entities**, find your battery entity, and confirm it is not marked as hidden.

### 3. HA API returning 500 on startup

If the logs show `ERROR ha_api: HA API returned 500` only on the first scan after boot, this is a startup race condition where Battery Sentinel Plus initialized before HA's API was fully ready. Click **Scan Now** in the Settings tab to trigger an immediate refresh. It should find your devices on the next attempt.

### 4. Another integration causing a persistent 500 error

If the logs show `ERROR ha_api: HA API returned 500` on every scan (not just startup), a misbehaving HA integration may be causing HA's `/api/states` endpoint to fail. This is not a Battery Sentinel Plus bug.

To identify the culprit, go to **Settings > Devices & Services** and disable integrations one at a time, doing a Scan Now after each. When the 500 clears, you have found the problem integration.

**Known culprits:**
- **Dyson (shenxn/ha-dyson):** This integration is no longer maintained and can cause persistent 500 errors on the states endpoint. Switch to the actively maintained fork **libdyson-wg/ha-dyson** on GitHub.

---

## Zigbee tab shows Z-Wave devices, or shows no devices

The Zigbee tab requires **Zigbee2MQTT** and will not work with ZHA or other Zigbee integrations. It relies on the Last Seen timestamp sensors that Z2M publishes for each device (`sensor.*_last_seen` with `platform: mqtt`). ZHA does not expose equivalent sensors.

If you enabled Zigbee monitoring but are not using Z2M, the tab may show unrelated entities or appear empty. Go to **Settings > Device Monitoring** and uncheck "Enable Zigbee monitoring" to hide the tab.

If you are using Z2M and the tab is still empty or showing wrong devices, make sure:
- Last Seen is enabled in your Z2M settings
- The Last Seen sensor entities are enabled (not hidden) in HA
- The Battery Sentinel Plus add-on has been restarted after enabling Zigbee monitoring

---

## HA Supervised install fails: "manifest for docker:X.Y.Z-cli not found"

The HA Supervisor builds add-ons by pulling a `docker:X.Y.Z-cli` image from Docker Hub that matches your installed Docker version. If that exact tag does not exist on Docker Hub (which can happen with short-lived Docker patch releases), the build fails before our Dockerfile even runs.

Run `sudo apt update && sudo apt upgrade` on your host to update Docker to a version that has a published CLI image. Then retry the add-on installation.

If updating is not possible, report the issue to the [Home Assistant Supervisor team](https://github.com/home-assistant/supervisor/issues) as the Supervisor's exact-version matching has no fallback when a tag is missing.
