#!/usr/bin/env python3
"""
daily-brief: a morning calendar brief for the family, built from iCal feeds.

Reads config.yaml (see config.example.yaml), fetches each calendar's .ics,
and prints (or emails) a plain-text brief for today.

Usage:
    python brief.py                      # print today's brief
    python brief.py --date 2026-10-12    # print the brief for a specific date
    python brief.py --send               # email the brief via SMTP
    python brief.py --no-weather         # skip the weather lookup (offline)

Setup: copy config.example.yaml to config.yaml and fill in your calendars'
private iCal URLs. config.yaml is gitignored -- never commit it.
"""

import argparse
import os
import re
import smtplib
import sys
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage
from urllib.parse import quote
from urllib.request import url2pathname
from zoneinfo import ZoneInfo

import requests
import yaml
from icalendar import Calendar
from recurring_ical_events import of as recurring_of

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_TOMORROW_KEYWORDS = ["tournament", "flight", "appointment",
                             "surgery", "holiday", "travel"]

# (keyword, emoji) pairs, checked in order. First match wins.
ACTIVITY_EMOJIS = [
    ("water polo", "\U0001F93D"),
    ("soccer", "\u26BD"),
    ("football", "\U0001F3C8"),
    ("basketball", "\U0001F3C0"),
    ("baseball", "\u26BE"),
    ("tennis", "\U0001F3BE"),
    ("volleyball", "\U0001F3D0"),
    ("swim", "\U0001F3CA"),
    ("lifting", "\U0001F3CB\uFE0F"),
    ("weights", "\U0001F3CB\uFE0F"),
    ("workout", "\U0001F3CB\uFE0F"),
    ("dance", "\U0001F483"),
    ("hip hop", "\U0001F483"),
    ("cheer", "\U0001F483"),
    ("tutor", "\U0001F4DA"),
    ("doctor", "\U0001F3E5"),
    ("dentist", "\U0001F3E5"),
    ("appointment", "\U0001F3E5"),
    ("surgery", "\U0001F3E5"),
    ("eye", "\U0001F441\uFE0F"),
    ("coffee", "\u2615"),
    ("birthday", "\U0001F382"),
    ("flight", "\u2708\uFE0F"),
    ("travel", "\u2708\uFE0F"),
    ("trip", "\u2708\uFE0F"),
    ("game", "\U0001F3DF\uFE0F"),
    ("match", "\U0001F3DF\uFE0F"),
    ("tournament", "\U0001F3C6"),
    ("photo", "\U0001F4F7"),
    ("dinner", "\U0001F37D"),
    ("party", "\U0001F389"),
]
DEFAULT_EMOJI = "\U0001F4C5"

# WMO weather codes -> (emoji, short condition word)
WEATHER_CODES = {
    0: ("\u2600\uFE0F", "Sunny"),
    1: ("\U0001F324\uFE0F", "Mostly Clear"),
    2: ("\u26C5", "Partly Cloudy"),
    3: ("\u2601\uFE0F", "Cloudy"),
    45: ("\U0001F32B\uFE0F", "Fog"),
    48: ("\U0001F32B\uFE0F", "Fog"),
    51: ("\U0001F326\uFE0F", "Drizzle"),
    53: ("\U0001F326\uFE0F", "Drizzle"),
    55: ("\U0001F326\uFE0F", "Drizzle"),
    56: ("\U0001F327\uFE0F", "Freezing Drizzle"),
    57: ("\U0001F327\uFE0F", "Freezing Drizzle"),
    61: ("\U0001F327\uFE0F", "Rain"),
    63: ("\U0001F327\uFE0F", "Rain"),
    65: ("\U0001F327\uFE0F", "Rain"),
    66: ("\U0001F327\uFE0F", "Freezing Rain"),
    67: ("\U0001F327\uFE0F", "Freezing Rain"),
    71: ("\U0001F328\uFE0F", "Snow"),
    73: ("\U0001F328\uFE0F", "Snow"),
    75: ("\U0001F328\uFE0F", "Snow"),
    77: ("\U0001F328\uFE0F", "Snow"),
    80: ("\U0001F326\uFE0F", "Showers"),
    81: ("\U0001F326\uFE0F", "Showers"),
    82: ("\U0001F326\uFE0F", "Showers"),
    85: ("\U0001F328\uFE0F", "Snow Showers"),
    86: ("\U0001F328\uFE0F", "Snow Showers"),
    95: ("\u26C8\uFE0F", "Thunderstorm"),
    96: ("\u26C8\uFE0F", "Thunderstorm"),
    99: ("\u26C8\uFE0F", "Thunderstorm"),
}


def expand_env_vars(obj):
    """Recursively expand ${VAR} / $VAR in every string in a config tree."""
    if isinstance(obj, dict):
        return {k: expand_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [expand_env_vars(v) for v in obj]
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    return obj


def load_config(path):
    if not os.path.exists(path):
        sys.exit(f"Config not found: {path}\n"
                 "Copy config.example.yaml to config.yaml and fill it in.")
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    cfg = expand_env_vars(cfg)
    if not cfg.get("calendars"):
        sys.exit("config.yaml needs at least one calendar under 'calendars:'.")
    cfg.setdefault("timezone", "America/New_York")
    cfg.setdefault("location", "New York, NY")
    cfg.setdefault("tomorrow_keywords", DEFAULT_TOMORROW_KEYWORDS)
    cfg.setdefault("venue_shortcuts", [])
    return cfg


# ---------------------------------------------------------------------------
# Calendar fetching
# ---------------------------------------------------------------------------

def fetch_ics(url):
    """Fetch .ics bytes from an https:// URL or a local file:// URL."""
    if url.startswith("file://"):
        path = url2pathname(url[len("file://"):])
        with open(path, "rb") as f:
            return f.read()
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.content


def _as_local(dt, tz):
    """Return an aware datetime in tz; naive datetimes are assumed local."""
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=tz)
    # date (all-day): midnight at start of that day
    return datetime(dt.year, dt.month, dt.day, tzinfo=tz)


def events_for_day(cal_bytes, day_start, day_end, tz, cal_name, kid,
                   is_primary, sport=""):
    """All events from one .ics overlapping [day_start, day_end)."""
    cal = Calendar.from_ical(cal_bytes)
    out = []
    for comp in recurring_of(cal).between(day_start, day_end):
        raw_start = comp["DTSTART"].dt
        all_day = isinstance(raw_start, date) and not isinstance(
            raw_start, datetime)
        dtend_prop = comp.get("DTEND")
        if dtend_prop is not None:
            raw_end = dtend_prop.dt
        else:
            dur_prop = comp.get("DURATION")
            if dur_prop is not None:
                raw_end = raw_start + dur_prop.dt
            else:
                # No end given: assume a one-day all-day event or a
                # one-hour timed event.
                raw_end = (raw_start + timedelta(days=1) if all_day
                           else raw_start + timedelta(hours=1))
        start, end = _as_local(raw_start, tz), _as_local(raw_end, tz)
        if end <= day_start or start >= day_end:
            continue
        summary = str(comp.get("SUMMARY", "")).strip()
        if not summary:
            continue
        out.append({
            "start": start.astimezone(tz),
            "end": end.astimezone(tz),
            "all_day": all_day,
            "summary": summary,
            "location": str(comp.get("LOCATION", "") or "").strip(),
            "description": str(comp.get("DESCRIPTION", "") or "").strip(),
            "kid": kid,
            "calendar": cal_name,
            "primary": is_primary,
            "sport": sport,
        })
    return out


# ---------------------------------------------------------------------------
# Dedup / sorting
# ---------------------------------------------------------------------------

def normalize_title(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def overlaps(a, b):
    return a["start"] < b["end"] and b["start"] < a["end"]


def dedupe(events):
    """Merge copies of the same event across calendars.

    Same normalized title + overlapping times -> one event, preferring the
    calendar marked primary: true (whose title is kept).
    """
    kept = []
    for ev in sorted(events, key=lambda e: (e["start"], e["end"])):
        dup_of = None
        for k in kept:
            if normalize_title(k["summary"]) == normalize_title(
                    ev["summary"]) and overlaps(k, ev):
                dup_of = k
                break
        if dup_of is None:
            kept.append(ev)
        elif ev["primary"] and not dup_of["primary"]:
            kept[kept.index(dup_of)] = ev
        # otherwise the existing (primary or earlier) copy wins
    return sorted(kept, key=lambda e: (e["start"], e["end"]))


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def activity_emoji(summary, sport=""):
    s = summary.lower()
    for keyword, emoji in ACTIVITY_EMOJIS:
        if keyword in s:
            return emoji
    if sport:
        for keyword, emoji in ACTIVITY_EMOJIS:
            if keyword in sport.lower():
                return emoji
    return DEFAULT_EMOJI


def short_venue(location, shortcuts):
    """Short display name for the title line (full address stays in the map link)."""
    loc = location.strip()
    if not loc:
        return ""
    low = loc.lower()
    for sc in shortcuts:
        if sc.get("match", "").lower() in low:
            return sc.get("short", loc)
    # Default: text before the first comma ("Blach Middle School, 1120 ..." -> "Blach Middle School")
    return loc.split(",")[0].strip()


def fmt_time(dt):
    return dt.strftime("%-I:%M %p")


def fmt_event(ev, shortcuts):
    venue = short_venue(ev["location"], shortcuts)
    if ev["all_day"]:
        lines = ["All day"]
    else:
        lines = [f"{fmt_time(ev['start'])} \u2013 {fmt_time(ev['end'])}"]
    title = (f"{activity_emoji(ev['summary'], ev.get('sport', ''))} "
             f"{ev['kid']} \u2014 {ev['summary']}")
    if venue:
        title += f" @ {venue}"
    lines.append(title)
    if ev["location"]:
        q = quote(ev["location"])
        lines.append(f"[\U0001F4CD {venue or ev['location']}]"
                     f"(https://www.google.com/maps/search/?api=1&query={q})")
    return "\n".join(lines)


def conflict_notes(events):
    notes = []
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            if a["kid"] == b["kid"] and overlaps(a, b):
                notes.append(
                    f"\u26A0\uFE0F CONFLICT: {a['kid']}'s "
                    f"\"{a['summary']}\" overlaps \"{b['summary']}\"")
    return notes


def handoff_notes(events, shortcuts):
    notes = []
    for prev, nxt in zip(events, events[1:]):
        gap = (nxt["start"] - prev["end"]).total_seconds() / 60
        v1 = short_venue(prev["location"], shortcuts)
        v2 = short_venue(nxt["location"], shortcuts)
        if 0 <= gap <= 30 and v1 and v2 and v1.lower() != v2.lower():
            notes.append(
                f"\U0001F4DD {prev['kid']}'s \"{prev['summary']}\" ends "
                f"{fmt_time(prev['end'])} at {v1}; {nxt['kid']}'s "
                f"\"{nxt['summary']}\" starts {fmt_time(nxt['start'])} at "
                f"{v2} \u2014 a handoff.")
    return notes


def matches_keywords(ev, keywords):
    hay = f"{ev['summary']} {ev['description']}".lower()
    return any(kw.lower() in hay for kw in keywords)


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, no API key)
# ---------------------------------------------------------------------------

# NWS alert event keywords -> emoji. US only; the lookup below is skipped
# silently anywhere else.
ALERT_EMOJIS = [
    ("heat", "\U0001F321\uFE0F"),
    ("freeze", "\U0001F976"),
    ("frost", "\U0001F976"),
    ("wind", "\U0001F4A8"),
    ("tornado", "\u26C8\uFE0F"),
    ("hurricane", "\U0001F300"),
    ("storm", "\u26C8\uFE0F"),
    ("flood", "\U0001F30A"),
    ("fire", "\U0001F525"),
    ("snow", "\u2744\uFE0F"),
    ("winter", "\u2744\uFE0F"),
    ("ice", "\u2744\uFE0F"),
]


def fetch_alerts(lat, lon):
    """Raw active NWS alerts for a US lat/lon; [] on any failure."""
    try:
        data = requests.get(
            "https://api.weather.gov/alerts/active",
            params={"point": f"{lat},{lon}"},
            headers={"User-Agent": "daily-brief-template"},
            timeout=20).json()
    except Exception:
        return []
    alerts = []
    for feat in data.get("features", []):
        props = feat.get("properties", {})
        if props.get("event") and props.get("expires"):
            alerts.append({"event": props["event"],
                           "expires": props["expires"]})
    return alerts


def format_alerts(raw_alerts, tz):
    """Turn raw NWS alerts into one-line brief flags (deduplicated)."""
    lines, seen = [], set()
    for a in raw_alerts:
        if a["event"] in seen:
            continue
        seen.add(a["event"])
        emoji = "\u26A0\uFE0F"
        name = a["event"].lower()
        for keyword, em in ALERT_EMOJIS:
            if keyword in name:
                emoji = em
                break
        try:
            exp = datetime.fromisoformat(a["expires"]).astimezone(tz)
            when = f"until {exp.strftime('%A')} {fmt_time(exp)}"
        except Exception:
            when = ""
        lines.append(f"{emoji} {a['event']} {when}".rstrip())
        if len(lines) == 3:
            break
    return lines


def get_weather(location, tz_name):
    """Returns (emoji, high, low, condition, alerts) for today, or None."""
    try:
        g = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1}, timeout=20).json()
        if not g.get("results"):
            return None
        lat, lon = g["results"][0]["latitude"], g["results"][0]["longitude"]
        f = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={"latitude": lat, "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,weathercode",
                    "temperature_unit": "fahrenheit",
                    "timezone": tz_name, "forecast_days": 1},
            timeout=20).json()["daily"]
        code = f["weathercode"][0]
        emoji, condition = WEATHER_CODES.get(code, ("\u26C5", "Variable"))
        alerts = format_alerts(fetch_alerts(lat, lon), ZoneInfo(tz_name))
        return (emoji, round(f["temperature_2m_max"][0]),
                round(f["temperature_2m_min"][0]), condition, alerts)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Compose + deliver
# ---------------------------------------------------------------------------

def compose_brief(cfg, today_events, tomorrow_events, weather, target_date):
    tz = ZoneInfo(cfg["timezone"])
    label = cfg.get("location_label") or cfg["location"]
    if weather:
        emoji, high, low, condition, alerts = weather
        weather_line = f"{emoji} {high}\u00B0 / {low}\u00B0, {condition} \u2014 {label}"
    else:
        weather_line = f"\u26C5 Weather unavailable \u2014 {label}"
        alerts = []

    shortcuts = cfg["venue_shortcuts"]
    parts = [weather_line]
    parts.extend(alerts)
    parts += ["",
              f"\U0001F4C5 Today's Schedule \u2014 "
              f"{target_date.strftime('%A, %B %-d')}", ""]
    if not today_events:
        parts.append("No events on the calendar today.")
    else:
        parts.append("\n\n".join(fmt_event(e, shortcuts)
                                 for e in today_events))
        notes = conflict_notes(today_events) + handoff_notes(today_events,
                                                             shortcuts)
        if notes:
            parts.append("")
            parts.extend(notes)

    keywords = cfg["tomorrow_keywords"]
    notable = [e for e in tomorrow_events if matches_keywords(e, keywords)]
    if notable:
        parts += ["",
                  f"\U0001F4C5 Tomorrow \u2014 "
                  f"{(target_date + timedelta(days=1)).strftime('%A, %B %-d')}",
                  "",
                  "\n\n".join(fmt_event(e, shortcuts) for e in notable)]
    return "\n".join(parts).rstrip() + "\n"


def send_email(cfg, subject, body):
    smtp = cfg["smtp"]
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp["from"]
    msg["To"] = smtp["to"]
    msg.set_content(body)
    with smtplib.SMTP(smtp.get("host"), int(smtp.get("port", 587)),
                      timeout=30) as s:
        s.starttls()
        s.login(smtp["username"], smtp["password"])
        s.send_message(msg)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Daily family calendar brief.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--date", help="Build the brief for YYYY-MM-DD (testing).")
    ap.add_argument("--send", action="store_true",
                    help="Email the brief via SMTP instead of just printing.")
    ap.add_argument("--no-weather", action="store_true",
                    help="Skip the weather lookup (offline testing).")
    args = ap.parse_args()

    cfg = load_config(args.config)
    tz = ZoneInfo(cfg["timezone"])

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=tz)
    else:
        target = datetime.now(tz)
    day_start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow_start = day_start + timedelta(days=1)
    day_after = day_start + timedelta(days=2)

    today_events, tomorrow_events = [], []
    for cal in cfg["calendars"]:
        try:
            raw = fetch_ics(cal["ical_url"])
        except Exception as exc:
            print(f"Warning: could not fetch '{cal.get('name')}': {exc}",
                  file=sys.stderr)
            continue
        for ev in events_for_day(raw, day_start, day_after, tz,
                                 cal.get("name", ""), cal.get("kid", "Family"),
                                 bool(cal.get("primary")),
                                 cal.get("sport", "")):
            if ev["start"] < tomorrow_start:
                today_events.append(ev)
            else:
                tomorrow_events.append(ev)

    today_events = dedupe(today_events)
    tomorrow_events = dedupe(tomorrow_events)

    weather = None if args.no_weather else get_weather(cfg["location"],
                                                      cfg["timezone"])
    brief = compose_brief(cfg, today_events, tomorrow_events, weather,
                          day_start)

    if args.send:
        if "smtp" not in cfg:
            sys.exit("No 'smtp:' section in config.yaml; cannot --send.")
        send_email(cfg,
                   f"Daily Brief \u2014 {day_start.strftime('%A, %B %-d')}",
                   brief)
        print("Brief emailed.")
    else:
        print(brief, end="")


if __name__ == "__main__":
    main()
