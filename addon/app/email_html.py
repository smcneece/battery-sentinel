"""HTML email template builders for Battery Sentinel notifications.
Each build_* function returns a complete HTML document string ready to pass
to a HA notify service as the 'html' data field."""

import datetime
import html as html_mod

from device_utils import device_is_low, level_str, level_color

_REPO_URL = "https://github.com/smcneece/battery-sentinel"

_FOOTER = (
    f"<tr><td style='background:#f5f5f5;padding:12px 20px;text-align:center;"
    f"border-top:1px solid #e0e0e0'>"
    f"<span style='color:#aaa;font-size:.78em'>"
    f"<a href='{_REPO_URL}' style='color:#58a6ff;text-decoration:none'>Battery Sentinel Plus</a>"
    f" &mdash; Home Assistant Device Monitor</span>"
    f"</td></tr></table></body></html>"
)

_HEADER_STYLE = (
    "<!DOCTYPE html><html><body style='margin:0;padding:20px;background:#efefef;"
    "font-family:Arial,Helvetica,sans-serif'>"
    "<table width='100%' cellpadding='0' cellspacing='0' style='max-width:640px;margin:0 auto;"
    "border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.15)'>"
    "<tr><td style='background:#1a1a2e;padding:18px 20px'>"
    "<span style='color:#fff;font-size:1.1em;font-weight:bold'>Battery Sentinel Plus</span>"
)



def _two_col_rows(devices: list) -> str:
    header_row = (
        "<tr style='background:#f8f8f8'>"
        "<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Device</th>"
        "<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Entity ID</th>"
        "<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Room</th>"
        "</tr>"
    )
    rows = "".join(
        f"<tr style='background:{'#f9f9f9' if i % 2 == 0 else '#fff'}'>"
        f"<td style='padding:7px 14px;color:#222'>{html_mod.escape(d['name'])}</td>"
        f"<td style='padding:7px 14px;color:#888;font-size:.85em'>{html_mod.escape(d['entity_id'])}</td>"
        f"<td style='padding:7px 14px;color:#666;font-size:.9em'>{html_mod.escape(d.get('area', ''))}</td>"
        f"</tr>"
        for i, d in enumerate(devices)
    )
    return header_row + rows


def build_unavailable_html(devices: list, now: datetime.datetime) -> str:
    timestamp = now.strftime("%B %d, %Y at %I:%M %p")
    return (
        f"{_HEADER_STYLE}"
        f"<div style='color:#888;font-size:.8em;margin-top:5px;padding-left:38px'>Device Unavailable Alert &mdash; {timestamp}</div>"
        f"</td></tr>"
        f"<tr><td style='background:#fff;padding:4px 0'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'>"
        f"<tr><td colspan='3' style='padding:16px 14px 6px;font-weight:bold;color:#cc8800;"
        f"border-bottom:2px solid #cc8800;font-size:.92em'>"
        f"&#9888; Went Unavailable <span style='font-weight:normal;color:#aaa'>({len(devices)})</span></td></tr>"
        f"{_two_col_rows(devices)}"
        f"</table></td></tr>"
        f"{_FOOTER}"
    )


def build_recovery_html(devices: list, now: datetime.datetime) -> str:
    timestamp = now.strftime("%B %d, %Y at %I:%M %p")
    return (
        f"{_HEADER_STYLE}"
        f"<div style='color:#888;font-size:.8em;margin-top:5px;padding-left:38px'>Device Recovery Alert &mdash; {timestamp}</div>"
        f"</td></tr>"
        f"<tr><td style='background:#fff;padding:4px 0'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'>"
        f"<tr><td colspan='3' style='padding:16px 14px 6px;font-weight:bold;color:#2a7d2a;"
        f"border-bottom:2px solid #2a7d2a;font-size:.92em'>"
        f"&#10003; Back Online <span style='font-weight:normal;color:#aaa'>({len(devices)})</span></td></tr>"
        f"{_two_col_rows(devices)}"
        f"</table></td></tr>"
        f"{_FOOTER}"
    )


def build_report_html(low: list, ok: list, settings: dict, now: datetime.datetime, include_all: bool) -> str:
    include_type = settings.get("report_include_battery_type", False)
    timestamp = now.strftime("%B %d, %Y at %I:%M %p")
    cols = 4 if include_type else 3

    def device_row(d, stripe):
        bg    = "#fff9f9" if stripe and device_is_low(d) else ("#f9f9f9" if stripe else "#fff")
        color = level_color(d)
        lvl   = level_str(d)
        area  = html_mod.escape(d.get("area") or "")
        btype = f"<td style='padding:7px 14px;color:#888;font-size:.85em'>{html_mod.escape(d.get('battery_type', ''))}</td>" if include_type else ""
        return (
            f"<tr style='background:{bg}'>"
            f"<td style='padding:7px 14px;color:#222'>{html_mod.escape(d['name'])}</td>"
            f"<td style='padding:7px 14px;color:#666;font-size:.9em'>{area}</td>"
            f"<td style='padding:7px 14px;font-weight:bold;text-align:right;color:{color}'>{lvl}</td>"
            f"{btype}</tr>"
        )

    def section(heading, accent, devices):
        if not devices:
            return ""
        type_th = f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Type</th>" if include_type else ""
        hdr = (
            f"<tr><td colspan='{cols}' style='padding:16px 14px 6px;font-weight:bold;"
            f"color:{accent};border-bottom:2px solid {accent};font-size:.92em'>"
            f"{heading} <span style='font-weight:normal;color:#aaa'>({len(devices)})</span></td></tr>"
            f"<tr style='background:#f8f8f8'>"
            f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Device</th>"
            f"<th style='padding:7px 14px;text-align:left;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Room</th>"
            f"<th style='padding:7px 14px;text-align:right;color:#aaa;font-weight:normal;font-size:.82em;border-bottom:1px solid #eee'>Level</th>"
            f"{type_th}</tr>"
        )
        rows = "".join(device_row(d, i % 2 == 0) for i, d in enumerate(devices))
        spacer = f"<tr><td colspan='{cols}' style='padding:8px'></td></tr>"
        return hdr + rows + spacer

    if not low and not ok:
        body = (
            f"<tr><td colspan='{cols}' style='padding:32px;text-align:center;color:#4c4;font-size:1.05em'>"
            f"&#10003; All batteries are OK &mdash; nothing to report."
            f"</td></tr>"
        )
    elif include_all:
        body = section("&#9888; Needs Attention", "#cc3333", low) + section("&#10003; All Batteries", "#555", ok)
    else:
        body = section("&#9888; Low Batteries", "#cc3333", low)

    return (
        f"{_HEADER_STYLE}"
        f"<div style='color:#888;font-size:.8em;margin-top:5px;padding-left:38px'>Daily Battery Report &mdash; {timestamp}</div>"
        f"</td></tr>"
        f"<tr><td style='background:#fff;padding:4px 0'>"
        f"<table width='100%' cellpadding='0' cellspacing='0'>{body}</table>"
        f"</td></tr>"
        f"{_FOOTER}"
    )
