# Battery Sentinel Plus Changelog

For full release notes and details on each version, see the [GitHub Releases page](https://github.com/smcneece/battery-sentinel/releases).

## 2026.07.7
- Fixed: several strings in the device table (UI / Email / Mobile notify labels), the alert threshold toolbar dropdown, and the Z-Wave ping interval description were not being translated and always appeared in English regardless of the selected language
- History range buttons (7d, 30d, 90d, 6m, 1y) are now translatable via locale keys; Spanish updated with correct abbreviations (1a for año); other languages can localize these to their own conventions (e.g. German: 7T/6M/1J)

## 2026.07.6
- Spanish (Español) translation contributed by IsaTTeN
- Interface translations (i18n): the UI language is auto-detected from the browser on first load and can be overridden in Settings > General; preference is saved per-browser; the language selector only shows languages with an actual translation file so users never see a no-op option; translations are flat JSON files in `addon/app/locales/`, any language is welcome via PR; missing keys fall back to English automatically; contributor guide in `addon/app/locales/TRANSLATING.md`

## 2026.07.5
- Battery quantity per device: a number input in the device panel lets you record how many batteries a device uses; shown as a "2×" prefix in the Battery Type column when quantity is greater than 1; defaults to 1 for all existing devices
- Multi-language support: the UI language is auto-detected from the browser on first load and can be overridden in Settings > General; preference is saved per-browser; the language selector only shows languages with an actual translation file; translations are flat JSON files in `addon/app/locales/`, any language is welcome via PR; missing keys fall back to English automatically; contributor guide in `addon/app/locales/TRANSLATING.md`

## 2026.07.4
- Alert threshold in the device modal is now a free-entry number field (1 to 99%) instead of a fixed dropdown; the Ignore option is a separate checkbox; the inline device list dropdown and bulk threshold setter have been extended from 60% to 95% in 5% increments; the default threshold setting in Settings is also now a free-entry number field

## 2026.07.3
- New setting in Settings > General: "Exclude Battery Notes sensors" (on by default); when the Battery Notes integration is also installed, its virtual battery sensors share device_class: battery and appear as duplicates in the device list; enabling this setting filters them out automatically using the entity registry platform field

## 2026.07.2
- Fixed: Zigbee tab showed no devices on large HA installs (500+ entities); the aiohttp WebSocket client has a default 4 MB message size cap and the HA entity registry response on large installs exceeded it, causing a MESSAGE_TOO_BIG disconnect before any data was read; the cap is now removed on the three WebSocket calls that fetch full registry lists (entity registry, device registry); hidden entity filtering on the Devices tab was also silently broken by the same limit for affected users

## 2026.07.1
- Settings page reorganized into four sub-tabs (General, Notifications, Daily Report, Device Monitoring) to reduce scrolling and make it easier to find specific settings; the active sub-tab is remembered across page loads
- Columns card renamed to Display and now includes the battery level color settings; column visibility and color thresholds are grouped together as display preferences
- Configurable battery level color thresholds in Settings: set the percentage where the level display switches from green to yellow and from yellow to red; defaults match the previous hardcoded values (yellow below 25%, red below 10%) so existing installs look identical until changed; binary sensors (Low/OK) are always red or green and are unaffected

## 2026.06.1
- Fixed: low battery alert emails for binary sensors (devices that report Low/OK rather than a percentage) no longer include "Threshold: 20%" in the message body, since a numeric threshold is not meaningful for these sensors
- Low battery alerts now enforce a 24-hour per-device cooldown: once an alert fires for a device, it will not re-fire for that device for 24 hours, even if the device bounces between Low and OK states within that window

## 2026.05.5
- Fixed: "Send unavailable notifications" checkbox introduced in 2026.05.4 was not saving; the value was silently discarded by the storage layer's field allowlist which did not include the new field

## 2026.05.4
- Per-device "Send unavailable notifications" checkbox in the device detail panel: uncheck for any device that is expected to go offline (a laptop, a phone, a tablet) and it will be silently skipped for both went-unavailable and back-online alerts; battery level monitoring and all other notifications are unaffected; checked by default so existing behavior is unchanged
- Device panel: Notes field moved above the notification controls
- Device panel: "Mute notifications" renamed to "Mute battery level notifications"; "Notifications" checkbox group renamed to "Battery level notifications" to distinguish them from the new unavailable notification control

## 2026.05.3
- Clicking the battery level in the device list now opens the device panel directly on the History tab; clicking the device name or anywhere else on the row still opens the Details tab as before
- Z-Wave ping: battery-powered nodes (those with a matching battery entity in Battery Sentinel) are now skipped during routine alive pings; they are still pinged immediately when detected dead, avoiding unnecessary radio traffic on locks, sensors, and remotes
- Z-Wave ping: when a node is first detected dead, an immediate targeted ping is sent rather than waiting up to 30 minutes for the next scheduled ping cycle; if the node responds, Z-Wave JS clears the dead status before the alert delay expires and no notification is sent
- Troubleshooting guide added (TROUBLESHOOTING.md): covers common issues including no devices appearing, hidden entities, persistent HA API errors, and the HA Supervised Docker manifest error

## 2026.05.2
- Help / About button added to the page header: opens a modal showing the current Battery Sentinel Plus version, HA version, and install mode, with links to the GitHub repository, README, Changelog, and issue tracker
- Fixed: battery entities that were hidden in Battery Sentinel and then deleted from HA were retained in storage indefinitely; deleted entities are now pruned automatically on the next scan, matching the existing behavior for Z-Wave and Zigbee nodes
- Fixed: if HA returned an empty entity list during startup or restart before it was fully ready, all stored devices were wiped from storage causing every device to appear as new on the next successful scan and triggering new device notifications
- Docker support (beta): Battery Sentinel Plus can now run as a plain Docker container against any HA instance; set HA_BASE_URL and HA_TOKEN environment variables to connect; install mode is shown in Help / About; see README for setup instructions
- Battery history chart: clicking a device and opening the History tab shows a line chart of battery level over time; range selector offers 7d, 30d, 90d, 6m, 1y, and a custom date range; recent data uses HA recorder history, longer ranges use HA long-term statistics; alert threshold shown as a dashed line; binary sensors displayed as a step chart with Low/OK labels
- Z-Wave node pinging: new option in Settings to periodically ping all alive Z-Wave nodes; presses the per-node ping button to prompt Z-Wave JS to immediately check reachability rather than waiting for its natural mesh update cycle; sleeping (battery-powered) nodes are skipped automatically; configurable interval (default 30 minutes)
- Column visibility settings (Notifications, Script, Room) now apply to the Z-Wave and Zigbee tabs as well as the Devices tab
- Zigbee tab: Status column moved before Last Seen column
- Notification titles updated to "Battery Sentinel Plus" throughout; Z-Wave and Zigbee node alerts restructured to a cleaner two-line body format (status on first line, device name on second)

## 2026.05.1
- Renamed to Battery Sentinel Plus to reflect the expanded feature set; slug, repo URL, and existing installations are unchanged
- Zigbee device monitoring: new Zigbee tab and Settings card monitors Zigbee devices via the Last Seen timestamps published by Zigbee2MQTT; fires an offline alert when a device has not checked in past the configured threshold, and a recovery alert when it comes back online; requires Last Seen to be enabled in Z2M and the Last Seen sensor entities to be enabled in HA (see Zigbee Monitoring Setup in the README)
- Zigbee monitoring filters to MQTT-platform entities only, so Z-Wave and other non-Zigbee entities with similar naming patterns are automatically excluded
- Per-device Zigbee settings in the Zigbee tab: notes, mute, and per-channel notification overrides (bell, email, mobile, script) matching the battery device panel
- Configurable Zigbee offline threshold (default 24 hours) and scan interval (default 30 minutes) independent from the battery check interval
- Suppress duplicate unavailable alerts: new option in Notification Settings (on by default) skips battery unavailable and recovery alerts for devices already tracked by Z-Wave or Zigbee monitoring, preventing double notifications when a monitored device goes offline
- Settings page: Z-Wave and Zigbee settings consolidated into one Device Monitoring card; bell, email, and mobile toggles moved to the per-node/per-device tab; alert delay unified into one global setting shared by battery, Z-Wave, and Zigbee monitors; script trigger unified into the global script in Notifications and now passes a `device_type` variable (`battery`, `z-wave`, or `zigbee`) so one script can handle all three
- Settings page: Columns card now stacks below the Daily Email Report card, reducing horizontal width on wide screens
- Settings page: Save Settings button is now sticky at the bottom of the viewport so it is always visible regardless of page height
- Add-on info page now links to the GitHub repository

## 2026.04.17
- Z-Wave node monitoring: new Settings card to monitor Z-Wave network health via node status sensors reported by Z-Wave JS; covers every Z-Wave device on your network; switches, dimmers, locks, sirens, and sensors -- not just battery-powered ones; dead node detection timing depends on Z-Wave JS and device type
- Monitors all `sensor.*_node_status` entities automatically; covers every Z-Wave device on your network, not just battery-powered ones; no per-device configuration required
- Configurable alert delay (default 5 minutes) to filter out brief communication blips; alerts fire once per dead event and reset automatically on recovery; a recovery notification is sent when a node comes back online
- Bell, email, mobile push, and script trigger options for Z-Wave node alerts; script receives device_name, entity_id, and node_status as variables
- Tab order is now Devices | Z-Wave | Settings; Z-Wave tab appears between Devices and Settings when Z-Wave monitoring is enabled
- Light and dark theme: the UI now follows the operating system or browser preference automatically via `prefers-color-scheme`; no setting required
- Default alert threshold for newly discovered devices changed from 15% to 20%; existing per-device thresholds are not affected
- Fixed: Z-Wave entity IDs that were renamed in HA persisted in storage indefinitely; stale entries are now pruned on each scan
- Entities marked as not visible in the HA entity registry are now excluded from Battery Sentinel automatically; useful for browser battery sensors and other entities you intentionally hide in HA
- Security: HTML-escaped device names, areas, and battery types in all outgoing emails and in the device list, hidden devices section, and conflict modal in the UI
- Mute remaining time now shown next to the muted bell icon in the device list (e.g. 1h 53m)
- Code modularisation: notification logic, HTML email builders, device utilities, and HA connection constants each moved to their own modules; main.py is now a thin orchestration layer only

## 2026.04.16
- Mute notifications per device: a new "Mute notifications" dropdown in the device panel lets you silence alerts for 1 hour, 3 hours, 8 hours, 1 day, 3 days, or 1 week; a small bell icon appears in the device list next to muted devices; mutes expire automatically
- Fixed: devices with UI notifications turned off were still appearing in the HA persistent low-battery notification; muted devices are also now excluded from it

## 2026.04.15
- Daily Email Report now has day-of-week scheduling: seven checkboxes (Mon through Sun) let you pick which days the report sends; defaults to all days so existing behavior is unchanged
- Fixed: email notify service dropdown now excludes only persistent_notification, mobile_app_*, and alexa_media_*; ISP-branded and custom-named email services such as zoom_internet were incorrectly filtered out in 2026.04.13
- Fixed: device list now re-renders immediately after saving settings, so changes to global script and other inherited settings are reflected without a manual page refresh
- Scan Now description updated to clarify it refreshes battery levels and discovers new devices without re-triggering notifications for already-flagged devices
- Check interval description now shows the valid range (1 to 120 minutes)

## 2026.04.13
- Device modal now shows manufacturer and model number (pulled from the HA device registry) below the entity ID, so you can identify hardware without leaving Battery Sentinel
- Recovery notification: when a device that triggered an unavailable alert comes back online, a separate bell and email notification is sent confirming it has recovered
- Configurable unavailable alert delay: a new "Alert delay" field (default 5 minutes) in Notification Settings lets you set how long a device must stay offline before the alert fires; devices that recover before the delay expires are silently ignored, avoiding noise from brief communication blips; set to 0 for immediate alerts as before
- Fixed: daily report send time now uses the HA configured timezone instead of the container's UTC clock; users outside UTC will no longer see the report fire at the wrong local time
- Email notify service dropdown now filters out non-email services (persistent_notification, mobile_app_*, alexa_media_*) to prevent misconfiguration
- Renamed "Daily Report" section and button to "Daily Email Report" / "Send Email Report Now" to make clear it requires an email service
- Battery Notes community database is now bundled with the add-on; no external network calls are made during the battery type lookup

## 2026.04.12
- Fixed: battery types auto-filled by the lookup were not being added to the battery type list, causing them to appear blank in the device list and as "(custom)" in the device detail panel
- The lookup now sweeps all devices in the cache for orphaned types (set on devices but missing from the list) and adds them in one pass, cleaning up types set by the previous broken run
- Fixed: the inline battery type selector in the device list now shows the current value even if it is not in the managed list, matching the device detail panel behavior

## 2026.04.11
- Battery type auto-lookup: new "Look Up Battery Types" button in Settings > General queries the Battery Notes community database (maintained by andrew-codechimp) and suggests battery types based on each device's manufacturer and model from the HA device registry
- Devices with no type set are updated automatically in the background; a brief status message shows how many were filled in
- Devices where your existing type differs from the database are shown in a conflict modal with per-row "Keep Mine" / "Use DB" buttons and bulk "Keep All Mine" / "Use All Database" options in the footer
- Fixed: device_id was not being passed through merge_entities into the device cache; this caused the lookup to silently find no matches and would have affected any future feature relying on device_id

## 2026.04.10
- Hide device: the Delete button in the device panel now hides the device instead of removing it from storage, preventing it from reappearing on the next scan; hidden devices are excluded from the list and do not trigger any alerts or notifications
- Restore hidden devices: a collapsible "Hidden devices (N)" section appears at the bottom of the device list whenever there are hidden devices; each entry has a Restore button that returns it to the main list immediately and a Delete button for permanent removal (with a confirmation prompt noting the device will reappear on the next scan if its entity still exists in HA)
- Fixed: clicking the UI, Email, or Mobile checkboxes in the Notifications column header was triggering a column sort instead of toggling all devices; the click handler now correctly ignores clicks that originate from inside a label or checkbox

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
