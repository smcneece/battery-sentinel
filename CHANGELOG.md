# Battery Sentinel — Changelog

## 2026.04.6
- Script trigger per device with global fallback — run any HA script when a device crosses its threshold
- Script column in the device list shows assigned script at a glance (device-specific in white, inherited global in gray, disabled shows "Off")
- Delete device from modal with inline confirmation — removes from Battery Sentinel tracking; device reappears on next scan if the HA entity still exists

## 2026.04.5
- Delete device from modal with inline two-step confirmation

## 2026.04.4
- Inline device rename — click the device name in the modal to edit; saves the friendly name back to Home Assistant via the entity registry (entity ID and automations are unaffected)
- Live device filter/search box above the device list

## 2026.04.2
- Per-device notification controls — individual UI, Email, and Mobile toggles per device
- Column header checkboxes to enable/disable a notification channel for all devices at once
- Consolidated UI notification — single HA persistent notification listing all low batteries, updates in place, auto-dismisses on recovery
- Email To and CC fields, per-device email address override
- Daily battery report — scheduled email with configurable time, low-only or full list, optional battery type
- Configurable check interval (default 10 minutes)
- Card-based Settings layout

## 2026.04.1
- Initial release
- Auto-discovery of all `device_class: battery` entities from Home Assistant
- Device list with sortable columns (name, level, room), resizable columns, color-coded battery levels
- Per-device modal: battery type, alert threshold, notes, last replaced date, Replaced/Recharged Today button
- Email alerts via any HA notify service
- Room/area display from the HA area registry
- Binary sensor support (Low/OK display)
