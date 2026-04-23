# Battery Sentinel - Home Assistant Add-on

Monitor and manage every battery-powered device in your Home Assistant installation. Battery Sentinel provides a dedicated management page accessible from the HA sidebar, with per-device tracking, configurable alerts, email notifications, and a daily battery report.

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/smcneece/battery-sentinel)](https://github.com/smcneece/battery-sentinel/releases)
[![GitHub](https://img.shields.io/github/license/smcneece/battery-sentinel)](LICENSE)

> [![Sponsor](https://img.shields.io/badge/Sponsor-💖-pink)](https://github.com/sponsors/smcneece) — If Battery Sentinel saves you from dead Z-Wave sensors, drained Zigbee devices, or a phone or tablet that's quietly hitting 10% in the background, consider sponsoring! Even a small one-time amount shows appreciation and keeps the project going. Check out my [other HA projects](https://github.com/smcneece?tab=repositories) while you're here.
>
> ⭐ **Finding this useful?** Star the repo so other HA users can find it!
> [![GitHub stars](https://img.shields.io/github/stars/smcneece/battery-sentinel?style=social)](https://github.com/smcneece/battery-sentinel/stargazers)

> ⚠️ **Supervisor Required** — Battery Sentinel is a Home Assistant **add-on** and requires a Supervisor-managed installation. It will **not** work on Home Assistant Core (Python package) or Home Assistant Container (Docker-only). If you are running **Home Assistant OS** or **Home Assistant Supervised**, you're good to go.

---

## Features

### Device Discovery and Display
- Auto-discovers all battery-powered devices from Home Assistant; no manual configuration required
- Handles both numeric sensors (percentage) and binary sensors (Low/OK)
- Color-coded battery level indicators: red below 10%, amber below 25%, green otherwise
- Room/area column sourced from the HA area registry
- Sortable columns: name, level, or room
- Resizable columns with widths saved across browser sessions
- Inline alert threshold selector per row, no need to open the device panel
- Live filter box above the device list — type any part of a device name to narrow the list instantly

### Per-device Management
Click any device in the list to open its detail panel.

- **Inline rename** — click the device name at the top of the panel to edit it; saving writes the new friendly name back to Home Assistant via the entity registry (entity ID is unchanged, so automations and dashboards are unaffected). Note: this renames the battery *entity* only, not the parent device — the device name in HA's device registry is separate and will not change
- Battery type dropdown (AA, AAA, C, 9V, CR2032, CR2025, CR123A, CR2, 18650, or custom)
- Per-device alert threshold (5% to 30%, or Ignore)
- Notes field for free-text information about the device or its battery
- Last replaced date, manually editable or stamped with the Replaced/Recharged Today button
- Per-device notification controls: UI, Email, and Mobile toggles; email address override; mobile app service selector

### Notifications
Battery Sentinel supports three notification channels, each configurable globally and per device.

- **UI notification** — a single consolidated HA persistent notification listing all currently low batteries, sorted lowest first; updates in place each check cycle and dismisses automatically when all batteries recover
- **Email** — fires once per device when its threshold is first crossed; resets when the battery recovers
- **Mobile push** — per-device push notification via any `mobile_app_*` notify service; a global default service can be set in Settings with a per-device override in the device panel
- Each device has individual UI / Email / Mobile toggles in the device list and in the device panel; column header checkboxes let you enable or disable a channel for all devices at once
- Configurable check interval (default 10 minutes)
- Optional alert when a new battery device is discovered

### Email
- Select any HA notify service from a dropdown (populated from your installed integrations)
- Global To address and CC field (comma-separated for multiple recipients)
- Per-device email address override
- HTML-formatted email body for proper line breaks in all email clients

### Daily Report
- Scheduled daily email report at a configurable time
- Choose between low batteries only or a full status list of all devices
- Optional battery type included in each line (e.g. `- Kitchen Smoke Detector (Living Room): 8% [9V]`)
- Report is sorted lowest battery first

### Script Triggers
Run any Home Assistant script when a device crosses its threshold — useful for Alexa announcements, SMS notifications, flashing lights, or any other automation.

- **Global script** — set once in Settings; runs for every device that has no per-device override
- **Per-device script** — overrides the global for that device; can also be set to *Disabled* to suppress the global for a specific device
- Rate-limited to **once per calendar day** per device — if a battery sits at the threshold and fluctuates up and down, the script fires only once that day
- The Script column in the device list shows the assigned script at a glance (device-specific in white, inherited global in gray, disabled shows "Off")

Battery Sentinel passes the following variables to the script automatically:

| Variable | Example value | Description |
|----------|--------------|-------------|
| `device_name` | `Back Porch Temp Sensor` | Friendly name of the device |
| `battery_level` | `8%` or `Low` | Current battery level |
| `battery_type` | `AA` | Battery type if set, otherwise blank |
| `area` | `Outside` | HA area/room if assigned |
| `entity_id` | `sensor.back_porch_battery` | HA entity ID |

**Example script** — Alexa announcement with a fallback SMS:

```yaml
alias: Battery Sentinel Low Battery Alert
sequence:
  - action: notify.alexa_media_kitchen
    data:
      message: >
        Attention: {{ device_name }} battery is low at {{ battery_level }}.
        {% if battery_type %}It uses {{ battery_type }} batteries.{% endif %}
      data:
        type: announce
  - action: notify.sms_gateway
    data:
      message: >
        Battery Sentinel: {{ device_name }} ({{ area }}) is at {{ battery_level }}.
        {% if battery_type %}Battery type: {{ battery_type }}.{% endif %}
        Entity: {{ entity_id }}
```

### Settings
- All configuration through the built-in Settings tab; no YAML to edit
- Battery type list is fully manageable: add or remove types
- Scan Now button for an immediate manual refresh
- Card-based layout fills the screen on desktop, wraps on mobile

---

## Installation

### Via App Store (Recommended)

**Option A — Shortcut button** (requires [My Home Assistant](https://my.home-assistant.io/) to be configured):

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsmcneece%2Fbattery-sentinel)

> ⚠️ **We are currently unable to confirm this button works on recent HA versions** — it opens the App Store but may not show the pre-filled add repository dialog. We have [filed a bug with Home Assistant](https://github.com/home-assistant/my.home-assistant.io/issues/698). If this button works for you, please let us know in [our issues](https://github.com/smcneece/battery-sentinel/issues). In the meantime, use Option B below.

**Option B — Manual repository add** (works on all installations):

1. In Home Assistant go to **Settings → Apps → Install App**
2. Click the **⋮** menu (top right) and select **Repositories**
3. Click **+ Add** (bottom right corner)
4. Paste `https://github.com/smcneece/battery-sentinel` in the box and click **Add**

Once the repository is added:

1. Find **Battery Sentinel** in the App Store and click it.
2. Click **Install** and wait a moment for it to download.
3. Enable **Start on boot** and **Auto-update**.
4. Enable **Show in sidebar** for quick access from the HA menu.
5. Click **Start**.
6. Click **Open Web UI** or use the Battery Sentinel link in the sidebar.

### Manual Installation

> ⚠️ **No automatic updates** — local add-ons are not tracked by the Supervisor. You will not receive update notifications; you must check [GitHub releases](https://github.com/smcneece/battery-sentinel/releases) manually and re-copy files for each new version. The repository install method above is strongly recommended.

1. Copy the `addon` folder from this repository to `/addons/battery-sentinel/` on your Home Assistant host.
2. Go to **Settings > Add-ons > Add-on Store**, click the menu and select **Check for updates**.
3. Battery Sentinel will appear under **Local add-ons**. Click **Install**, then **Start**.

---

## Data & Backups

Battery Sentinel stores all device metadata (notes, battery types, alert thresholds, last replaced dates) in a single file within the add-on's data directory. This file is included in standard Home Assistant full backups — no special steps required.

---

## Requirements

- **Home Assistant OS** or **Home Assistant Supervised** — the Supervisor is required to install and run add-ons. Home Assistant Core and Home Assistant Container installations cannot use add-ons.
- No additional configuration; the add-on connects to HA automatically via the Supervisor API

---

## Configuration

All configuration is done within the add-on UI. There is no YAML to edit.

### General (Settings tab)

| Setting | Default | Description |
|---------|---------|-------------|
| Battery types | AA, AAA, C, 9V, CR2032, CR2025, CR123A, CR2, 18650 | Managed list available in the per-device dropdown |
| Check interval | 10 min | How often the add-on scans for low batteries and updates the notification |
| Scan Now | button | Triggers an immediate refresh of all device data |

### Notifications (Settings tab)

| Setting | Default | Description |
|---------|---------|-------------|
| Default alert threshold | 15% | Alert level applied to newly discovered devices |
| UI notification | On | Creates/updates a single HA persistent notification listing all low batteries |
| New device alert | On | Fires a notification when a new battery device is first discovered |
| Email notify service | none | Dropdown of your installed HA notify services |
| Default To address | none | Primary email recipient for all alerts and reports |
| CC addresses | none | Additional recipients, comma-separated |
| Default mobile service | none | Fallback `mobile_app_*` service used when a device has Mobile enabled but no specific service set |
| Global script trigger | none | Script to run when any device crosses its threshold; per-device setting overrides this |

### Daily Report (Settings tab)

| Setting | Default | Description |
|---------|---------|-------------|
| Send daily report | Off | Enables the scheduled daily email |
| Send time | 08:00 | Time of day to send the report |
| Include | Low batteries only | Choose low batteries only or a full status list |
| Include battery type | Off | Appends the battery type to each line when set |

### Per-device settings (device detail panel)

| Field | Description |
|-------|-------------|
| Battery type | Dropdown populated from your configured battery type list |
| Alert threshold | Per-device override: 5% to 30% in 5% increments, or Ignore |
| Notifications | UI, Email, and Mobile toggles for this device specifically |
| Email address override | Sends this device's alerts to a specific address instead of the global default |
| Mobile app | Select a `mobile_app_*` notify service to receive push notifications for this device |
| Script trigger | Use global default, select a specific script to override, or set to Disabled to suppress the global for this device |
| Notes | Free-text field for any relevant notes |
| Last replaced | Date of last battery replacement; set automatically via the button or edited manually |
| Replaced/Recharged Today | Stamps today's date as the last replacement date |
| Delete | Removes the device from Battery Sentinel. Use this to clear out devices that have been removed from Home Assistant or that you no longer want to track. The device will reappear automatically on the next scan if its entity still exists in HA. |

---

## Browser Support

Battery Sentinel is fully functional on mobile and tablet browsers. For the best experience, a desktop browser is recommended — the device table with resizable columns, battery level bars, and notification checkboxes is designed for wider screens. On narrow mobile screens, columns will be compressed and some detail (such as the level bar) may be partially clipped.

---

## Supported Device Types

Battery Sentinel discovers any entity in Home Assistant with `device_class: battery`. This includes:

- Z-Wave and Zigbee sensors, remotes, and door/window contacts
- Bluetooth devices
- Matter devices
- Wi-Fi devices that report battery level (phones, tablets, weather stations)
- Any integration that correctly sets the battery device class

Devices that report battery as a binary state (low/ok) rather than a percentage are displayed with a Low/OK indicator instead of a percentage bar, and sort to the top of the list when low.

---

## How Notifications Work

Battery Sentinel avoids notification spam by design across all three channels.

- On each check cycle, the add-on builds a list of every device currently below its threshold
- **UI:** a single notification titled **Battery Sentinel: Low Batteries** is created or updated in place with that list; it dismisses automatically when all batteries recover
- **Email:** each device fires one email when it first crosses its threshold and resets when the battery recovers or is replaced — no repeat emails for a battery that stays low
- **Mobile:** same single-fire behaviour as email; uses the device-specific `mobile_app_*` service if set, otherwise falls back to the global default
- The check interval is configurable; changing it in Settings takes effect after the current cycle completes without restarting the add-on

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

---

## Support

- **Issues & bug reports**: [GitHub Issues](https://github.com/smcneece/battery-sentinel/issues)
- **Feature requests & questions**: [GitHub Discussions](https://github.com/smcneece/battery-sentinel/discussions)
- **Community**: [Home Assistant Community Forum](https://community.home-assistant.io/)

---

## Keywords

**Devices:** Z-Wave, Zigbee, Bluetooth, Matter, Wi-Fi sensors, door/window contacts, remotes, smoke detectors, phones, tablets, weather stations  
**Battery types:** AA, AAA, CR2032, CR2025, CR123A, CR2, 9V, 18650, rechargeable  
**Software:** Home Assistant, Home Assistant add-on, Supervisor, Home Assistant OS, Home Assistant Supervised, ingress UI  
**Features:** Battery monitor, battery tracker, battery replacement, low battery alert, battery notification, battery report, email alert, mobile push notification, HA script trigger

<!-- 
SEO Keywords: home assistant battery monitor, home assistant battery tracker, battery sentinel,
home assistant add-on, supervisor add-on, ha addon, battery level monitor, low battery notification,
battery replacement tracker, z-wave battery, zigbee battery, bluetooth battery, matter battery,
home assistant battery alert, battery email notification, battery push notification,
battery device management, ha ingress, home assistant sidebar, battery report, daily battery report,
battery threshold, battery type tracker, home assistant battery management,
smcneece, battery-sentinel, battery sentinel addon, home assistant battery addon,
CR2032 tracker, AA battery monitor, rechargeable battery tracker, binary sensor battery,
home assistant battery percentage, home assistant battery low alert
-->

