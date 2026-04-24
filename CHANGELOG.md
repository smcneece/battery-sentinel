# Battery Sentinel Changelog

## 2026.04.9
- Unavailable and unknown battery entities now appear in the device list with an N/A indicator; they do not trigger alerts or notifications
- Optional alert when a battery device goes unavailable or unknown: Settings checkbox, fires bell and HTML-formatted email notification (off by default); suppressed on the first scan after startup to avoid false positives while integrations are still loading
- Responsive layout for mobile and tablet: columns are hidden progressively on smaller screens, toolbar stacks vertically, padding and font sizes scale down
- Frontend HTML and JavaScript extracted into a standalone `index.html` file, separate from the Python server
- Dockerfile updated to use the official Home Assistant base Python image

## 2026.04.8
- Alert threshold range extended from 30% to 60% (5% increments): useful for safety-critical devices like smoke detectors where proactive replacement makes sense
- Battery Type column in device list with inline dropdown: set or change a device's battery type without opening the detail panel
- Battery Type column header filter: narrow the list to a specific type or find all devices with no type assigned (Unassigned)
- Bulk Battery Type setter: toolbar dropdown to apply a battery type to all name-filtered devices at once with a single confirmation
- Bulk Alert Threshold setter: same toolbar pattern for thresholds; filter by name, pick a level, apply to all matching devices
- Column visibility controls in Settings: checkboxes to show or hide any column; saved in the browser across sessions
- Fixed: battery sensors reporting decimal levels (e.g. `95.0`) were not sorted correctly and displayed gray instead of color-coded levels in the device list

## 2026.04.7
- HTML-formatted daily report email: dark header with icon and timestamp, color-coded battery levels (red/amber/green), two sections (Needs Attention + All Batteries) when full-list mode is enabled, footer with project link
- "Send Report Now" button in Daily Report settings: sends the report immediately without waiting for the scheduled time; useful for testing email configuration
- "Send report even when all batteries are OK" option: when disabled (default), the low-only report is suppressed if nothing is low
- Add-on icon displayed in the web UI header
- Fixed: battery sensors reporting decimal levels (e.g. `95.0%` instead of `95%`) were not sorted correctly, displayed gray instead of color-coded levels, and were not detected as low even when below threshold

## 2026.04.6
- Script trigger per device with global fallback: run any HA script when a device crosses its threshold
- Script column in the device list shows assigned script at a glance (device-specific in white, inherited global in gray, disabled shows "Off")
- Delete device from modal with inline confirmation: removes from Battery Sentinel tracking; device reappears on next scan if the HA entity still exists

## 2026.04.5
- Delete device from modal with inline two-step confirmation

## 2026.04.4
- Inline device rename: click the device name in the modal to edit; saves the friendly name back to Home Assistant via the entity registry (entity ID and automations are unaffected)
- Live device filter/search box above the device list

## 2026.04.2
- Per-device notification controls: individual UI, Email, and Mobile toggles per device
- Column header checkboxes to enable/disable a notification channel for all devices at once
- Consolidated UI notification: single HA persistent notification listing all low batteries, updates in place, auto-dismisses on recovery
- Email To and CC fields, per-device email address override
- Daily battery report: scheduled email with configurable time, low-only or full list, optional battery type
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
