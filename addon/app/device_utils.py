"""Pure device-state utility functions -- no HA API calls, no I/O.
Kept in its own module so ha_api, notifications, and email_html can all import
without creating circular dependencies."""


def device_is_low(device: dict) -> bool:
    """Return True if the device is currently below its alert threshold.
    Unavailable/unknown states are never treated as low -- they have their own alert path."""
    if device["state"] in ("unavailable", "unknown"):
        return False
    threshold = device.get("alert_threshold", 15)
    if threshold == -1:  # -1 means "Ignore" -- alerts disabled for this device
        return False
    # Binary sensors (e.g. simple low/OK sensors) report "on" when low
    if device["entity_id"].startswith("binary_sensor."):
        return device["state"] == "on"
    try:
        return float(device["state"]) < threshold
    except (ValueError, TypeError):
        return False


def level_str(device: dict) -> str:
    """Human-readable battery level string for notifications and reports."""
    if device["state"] in ("unavailable", "unknown"):
        return "Unavailable"
    if device["entity_id"].startswith("binary_sensor."):
        return "Low" if device["state"] == "on" else "OK"
    try:
        return f"{float(device['state']):.0f}%"
    except (ValueError, TypeError):
        return f"{device['state']}%"


def level_color(device: dict, red: int = 10, yellow: int = 25) -> str:
    """CSS colour for the battery level -- used in HTML email templates."""
    if device["entity_id"].startswith("binary_sensor."):
        return "#cc3333" if device["state"] == "on" else "#4c4"
    try:
        pct = float(device["state"])
        if pct < red:    return "#cc3333"
        if pct < yellow: return "#cc8800"
        return "#4c4"
    except (ValueError, TypeError):
        return "#888"


def report_sort_key(device: dict) -> float:
    """Sort key that puts lowest batteries first, binary-low next, then OK, then unavailable."""
    if device["state"] in ("unavailable", "unknown"):
        return 102  # bottom of list
    if device["entity_id"].startswith("binary_sensor."):
        return 0 if device["state"] == "on" else 101  # low binary floats to top, OK binary near bottom
    try:
        return float(device["state"])
    except (ValueError, TypeError):
        return 100


def count_noun(n: int, singular: str, plural: str = "") -> str:
    """'1 device' / '3 devices' -- count followed by the correctly pluralised noun."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def md_link(text: str, link: str) -> str:
    """Render text as a markdown link, escaping brackets in the text; plain text when link is empty."""
    if not link:
        return text
    escaped = text.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"[{escaped}]({link})"


def format_line(device: dict, include_type: bool, link: str = "") -> str:
    """Single text line for a device, used in bell notifications and plain-text emails.

    When link is given, the device name is rendered as a markdown link to it."""
    area  = f" ({device['area']})" if device.get("area") else ""
    btype = f" [{device['battery_type']}]" if include_type and device.get("battery_type") else ""
    return f"- {md_link(device['name'], link)}{area}: {level_str(device)}{btype}"
