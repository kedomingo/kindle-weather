#!/usr/bin/env python3
"""Generate the Kindle weather image.

  ./weather.py --config config.json          fetch, render, write the PNG
  ./weather.py --dry-run                     print the summary, draw nothing
  ./weather.py --fixture sample.json         render from a saved payload

On a failed fetch the last good payload is reused and the image is marked
offline, so a flaky wifi moment leaves a slightly stale display rather than a
blank one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import advisor
import gcal
import openmeteo
from advisor import DayWindow

HERE = os.path.dirname(os.path.abspath(__file__))

# Read .env beside the script rather than in the working directory, so the
# systemd timer finds it too. Real environment variables win over the file,
# which lets a unit override one value with Environment= without editing it.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:  # pragma: no cover - a missing extra, not a missing feature
    print("python-dotenv is not installed; .env will be ignored "
          "(pip install -r requirements.txt)", file=sys.stderr)

# The calendar's ID is not a secret in the cryptographic sense, but it is the
# one string that turns a public calendar into a readable one, and credentials
# for a private calendar genuinely are secret. Both belong in .env, so that a
# config file stays safe to commit.
ENV_CALENDAR = {
    "calendar_id": "CALENDAR_ID",
    "ics_url": "CALENDAR_ICS_URL",
    "api_key": "CALENDAR_API_KEY",
    "client_id": "CALENDAR_CLIENT_ID",
    "client_secret": "CALENDAR_CLIENT_SECRET",
    "refresh_token": "CALENDAR_REFRESH_TOKEN",
}

DEFAULTS = {
    "city": "Manila",
    "latitude": 14.5995,
    "longitude": 120.9842,
    "timezone": "Asia/Manila",
    "output": "weather-script-output.png",
    "cache": "last-forecast.json",
    "display_units": "metric",
    "rotate": 90,
    "width": 800,
    "height": 600,
    "greys": 16,
    "max_advisories": 3,
    "day_start_hour": 6,
    "day_end_hour": 21,
    "commutes": [[7, 9], [17, 19]],
    "retries": 3,
    "fonts": {"regular": None, "bold": None},
    # Resolved against the script, not the cwd, so systemd finds it too. Set
    # to null for a plain display with no cat.
    "cat_dir": os.path.join(HERE, "cat"),
    # null disables the calendar band. For a public calendar, all it needs is
    # {"calendar_id": "...", "max_events": 2} -- paste either the ID or the
    # share link Google gives you. A private calendar needs the OAuth fields
    # instead; see gcal_setup.py.
    "calendar": None,
}


def load_config(path: str | None) -> dict:
    config = dict(DEFAULTS)
    if path:
        with open(path) as fh:
            config.update(json.load(fh))
    return config


def local_now(tz_offset_seconds: int) -> datetime:
    """Wall-clock time in the forecast's timezone.

    Taken from the API's utc_offset_seconds rather than the Pi's clock, so the
    display is right even if the box itself is set to UTC.
    """
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(seconds=tz_offset_seconds))).replace(tzinfo=None)


def fetch_with_retry(config: dict, attempts: int) -> dict:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return openmeteo.fetch_raw(config["latitude"], config["longitude"], config["timezone"])
        except Exception as exc:  # network, HTTP, malformed JSON
            last = exc
            print(f"fetch attempt {i + 1}/{attempts} failed: {exc}", file=sys.stderr)
            if i + 1 < attempts:
                time.sleep(5 * (i + 1))
    raise RuntimeError(f"could not reach Open-Meteo: {last}")


def write_atomic(img, path: str) -> None:
    """Write the PNG next to its destination, then rename into place, so the
    Kindle never fetches a half-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".png")
    os.close(fd)
    try:
        img.save(tmp, format="PNG", optimize=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render a weather image for a jailbroken Kindle")
    ap.add_argument("--config")
    ap.add_argument("--output", help="override the configured output path")
    ap.add_argument("--fixture", help="render from a saved Open-Meteo payload instead of fetching")
    ap.add_argument("--dry-run", action="store_true", help="print the summary, write no image")
    ap.add_argument("--cat", help="use this cat PNG instead of a random one")
    ap.add_argument("--events-fixture", help="render events from a saved JSON list instead of fetching")
    ap.add_argument("--no-calendar", action="store_true", help="skip the calendar entirely")
    args = ap.parse_args(argv)

    config = load_config(args.config)
    window = DayWindow(
        start_hour=config["day_start_hour"],
        end_hour=config["day_end_hour"],
        commutes=tuple(tuple(c) for c in config["commutes"]),
    )

    # Read up front: the previous events are the fallback if the calendar call
    # fails while the weather call succeeds.
    cached = _load_cache(config.get("cache"))

    stale = False
    fresh = False
    if args.fixture:
        raw = json.load(open(args.fixture))
        fetched_at = local_now(raw.get("utc_offset_seconds", 0))
    else:
        try:
            raw = fetch_with_retry(config, config["retries"])
            fetched_at = local_now(raw["utc_offset_seconds"])
            fresh = True
        except RuntimeError as exc:
            print(exc, file=sys.stderr)
            if cached is None:
                return 1
            raw, fetched_at, stale, fresh = cached[0], cached[1], True, False
            print(f"falling back to cached forecast from {fetched_at:%Y-%m-%d %H:%M}", file=sys.stderr)

    now = local_now(raw["utc_offset_seconds"])
    try:
        forecast = openmeteo.parse(raw, now.date(), fetched_at)
    except ValueError as exc:
        # Only reachable with a cached payload too old to cover today.
        print(f"{exc}; refusing to draw a stale day", file=sys.stderr)
        return 1
    units = advisor.Units(imperial=config["display_units"] == "imperial")
    summary = advisor.summarise(forecast, window, config["max_advisories"], units)

    events = load_events(config, args, now, raw["utc_offset_seconds"],
                         cached[2] if cached else [])
    if not args.fixture and fresh and config.get("cache"):
        _save_cache(config["cache"], raw, fetched_at, events)


    if args.dry_run:
        print(f"{config['city']}  {now:%a %d %b %H:%M}" + ("  [OFFLINE]" if stale else ""))
        print(f"  high {units.deg(forecast.today.temp_max)}  low {units.deg(forecast.today.temp_min)}")
        print(f"  {summary.headline}")
        for a in summary.advisories:
            print(f"  - {a.text}   ({a.kind}, severity {a.severity})")
        if summary.quiet:
            print("  - nothing to prepare for")
        for e in events:
            when = "all day" if e.all_day else f"{e.start:%H:%M}"
            print(f"  @ {when}  {e.title}")
        return 0

    import render  # imported late so --dry-run works without Pillow installed

    opts = render.RenderOptions(
        city=config["city"],
        window=window,
        width=config["width"],
        height=config["height"],
        rotate=config["rotate"],
        imperial=config["display_units"] == "imperial",
        stale=stale,
        greys=config["greys"],
        font_regular=config["fonts"].get("regular"),
        font_bold=config["fonts"].get("bold"),
        cat_dir=config.get("cat_dir"),
        cat_pick=args.cat,
        events=tuple(events),
    )
    img = render.render(forecast, summary, opts, now)
    out = args.output or config["output"]
    write_atomic(img, out)
    print(f"wrote {out} ({img.width}x{img.height}, {os.path.getsize(out)} bytes)")
    return 0


def calendar_settings(config: dict) -> dict:
    """The calendar settings, with .env overriding the JSON config.

    Environment last because it is the per-machine, uncommitted half of the
    configuration: a CALENDAR_ID in .env is enough on its own, and switches the
    band on even when the config file says nothing about a calendar.
    """
    settings = dict(config.get("calendar") or {})
    for key, var in ENV_CALENDAR.items():
        value = os.environ.get(var, "").strip()
        if value:
            settings[key] = value
    limit = os.environ.get("CALENDAR_MAX_EVENTS", "").strip()
    if limit:
        try:
            settings["max_events"] = int(limit)
        except ValueError:
            print(f"ignoring CALENDAR_MAX_EVENTS={limit!r}: not a number", file=sys.stderr)
    return settings


def load_events(config: dict, args, now: datetime, tz_offset: int, fallback: list) -> list:
    """Today's next few events, or nothing at all.

    Best effort by design: the calendar is an extra, and a Google outage or an
    expired refresh token must cost the band, never the weather. On failure the
    previously cached events are reused so one flaky call does not blank it.
    """
    if args.events_fixture:
        return [gcal.Event.from_dict(e) for e in json.load(open(args.events_fixture))]
    settings = calendar_settings(config)
    if args.no_calendar or not settings.get("calendar_id") and not settings.get("ics_url"):
        # Said out loud only on a dry run: an empty band is indistinguishable
        # from a broken one, and this is the likeliest reason for it. Staying
        # quiet in normal operation keeps the render timer's log clean.
        if args.dry_run:
            reason = ("--no-calendar" if args.no_calendar
                      else "no CALENDAR_ID in .env and none in the config")
            print(f"calendar band off ({reason})", file=sys.stderr)
        return []
    limit = settings.get("max_events", 2)
    try:
        return gcal.fetch(settings, now.date(), tz_offset, now, limit)
    except Exception as exc:  # network, HTTP, malformed JSON, bad credentials
        print(f"calendar unavailable: {exc}", file=sys.stderr)
        return [e for e in fallback if e.end > now][:limit]


def _save_cache(path: str, raw: dict, fetched_at: datetime, events=()) -> None:
    payload = {"fetched_at": fetched_at.isoformat(), "raw": raw,
               "events": [e.as_dict() for e in events]}
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(payload, fh)


def _load_cache(path: str | None) -> tuple[dict, datetime, list] | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            payload = json.load(fh)
        events = [gcal.Event.from_dict(e) for e in payload.get("events", [])]
        return payload["raw"], datetime.fromisoformat(payload["fetched_at"]), events
    except Exception as exc:
        print(f"cache unusable: {exc}", file=sys.stderr)
        return None


if __name__ == "__main__":
    sys.exit(main())
