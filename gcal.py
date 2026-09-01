"""Read today's calendar events, using nothing but the standard library.

Three ways in. Prefer the first: a static API key identifies the project rather
than a person, so neither of the first two reaches anything that is not already
public, and only the third can open a private calendar.

  ics_url       For a calendar you have made public, read as .ics. Needs no
                credentials of any kind -- no Cloud project, no key, nothing
                that expires -- so this is the one to use. Set calendar_id and
                the URL is derived for you.

  api_key       The same public calendar via the JSON API. Worth it only if
                recurring events on it are complicated enough that you would
                rather Google expand them than ics.py (see its docstring).

  Public really is public, for both: anyone holding the calendar ID can read
  every title and time on it, so put only the events you want on the display
  there.

  refresh_token For a private calendar, which the API will not open to a key at
                any price. Needs a one-time consent (see gcal_setup.py). Watch
                the publishing status of the consent screen: an "External" app
                left in "Testing" is issued refresh tokens that expire after 7
                days, which surfaces as a display that goes blank every week
                for no visible reason. Set it to "In production".

Either way the API itself is free -- a million requests a day per project, and
no billing account -- so a render every half hour costs nothing.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import ics

TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
ICS_URL = "https://calendar.google.com/calendar/ical/{calendar}/public/basic.ics"
SCOPE = "https://www.googleapis.com/auth/calendar.readonly"

# Asked for well above the display cap, because declined and already-finished
# events are filtered out below and each one would otherwise eat a slot.
FETCH_LIMIT = 25


@dataclass
class Event:
    title: str
    start: datetime          # local wall clock; midnight if all_day
    end: datetime
    all_day: bool = False

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Event":
        return cls(
            title=raw["title"],
            start=datetime.fromisoformat(raw["start"]),
            end=datetime.fromisoformat(raw["end"]),
            all_day=raw.get("all_day", False),
        )


def fetch(config: dict, day: date, tz_offset: int, now: datetime, limit: int) -> list[Event]:
    """Today's next `limit` events, soonest first.

    Events that have already finished are dropped: at 3pm a display reminding
    you about the 9am standup is just noise. If everything today is over, the
    band renders empty rather than showing history.
    """
    if config.get("api_key") or config.get("refresh_token"):
        events = _from_api(config, day, tz_offset)
    else:
        events = _from_ics(config, day, tz_offset)
    return [e for e in events if e.end > now][:limit]


def _from_ics(config: dict, day: date, tz_offset: int) -> list[Event]:
    """The public .ics feed, parsed locally."""
    url = config.get("ics_url") or ICS_URL.format(
        calendar=urllib.parse.quote(_calendar_id(config)))
    req = urllib.request.Request(url, headers={"User-Agent": "weather-display"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            text = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        hint = " The calendar must be public for this URL to answer." if exc.code == 404 else ""
        raise RuntimeError(f"calendar feed failed ({exc.code}).{hint}") from exc
    return [Event(e["title"], e["start"], e["end"], e["all_day"])
            for e in ics.parse(text, day, tz_offset)]


def _calendar_id(config: dict) -> str:
    """The calendar's ID, accepting the share link Google hands you.

    That link carries the ID base64'd in its cid= parameter, and pasting the
    link is the obvious thing to try, so decode it rather than fail on it.
    """
    value = config.get("calendar_id") or ""
    if not value:
        raise RuntimeError("calendar needs a calendar_id (or an ics_url)")
    if "cid=" in value:
        cid = urllib.parse.parse_qs(urllib.parse.urlparse(value).query).get("cid", [""])[0]
        padded = cid + "=" * (-len(cid) % 4)
        try:
            return base64.urlsafe_b64decode(padded).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"could not read a calendar ID out of {value!r}") from exc
    return value


def _from_api(config: dict, day: date, tz_offset: int) -> list[Event]:
    """The JSON API, which expands recurrence server-side but needs a key."""
    offset = _offset(tz_offset)
    params = {
        "timeMin": f"{day.isoformat()}T00:00:00{offset}",
        "timeMax": f"{(day + timedelta(days=1)).isoformat()}T00:00:00{offset}",
        # orderBy=startTime is only allowed alongside singleEvents, which also
        # expands a recurring event into the instance that falls today.
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(FETCH_LIMIT),
    }
    headers = {}
    if config.get("api_key"):
        params["key"] = config["api_key"]
    else:
        headers["Authorization"] = f"Bearer {_access_token(config)}"

    calendar = urllib.parse.quote(
        _calendar_id(config) if config.get("api_key") else config.get("calendar_id", "primary"))
    url = EVENTS_URL.format(calendar=calendar) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        hint = ""
        if exc.code in (403, 404) and config.get("api_key"):
            # The usual cause by a mile: a key can only see a public calendar.
            hint = (" With an api_key the calendar must be public and the "
                    "calendar_id must be its ID, not your email address.")
        raise RuntimeError(f"calendar request failed ({exc.code}): {detail}.{hint}") from exc

    events = []
    for item in payload.get("items", []):
        event = _parse_event(item, tz_offset)
        if event is not None:
            events.append(event)
    return events


def _access_token(config: dict) -> str:
    """Trade the long-lived refresh token for a one-hour access token."""
    body = urllib.parse.urlencode({
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "refresh_token": config["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as exc:
        # invalid_grant here almost always means the refresh token was revoked
        # or expired, and the 7-day expiry on a "Testing" app is the usual why.
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise RuntimeError(
            f"refreshing the Google token failed ({exc.code}): {detail}. "
            f"If this says invalid_grant, re-run gcal_setup.py and check the "
            f"OAuth consent screen is published, not in Testing."
        ) from exc


def _parse_event(item: dict, tz_offset: int) -> Event | None:
    if item.get("status") == "cancelled":
        return None
    if _declined(item):
        return None

    start_raw, end_raw = item.get("start") or {}, item.get("end") or {}
    if "dateTime" in start_raw:
        start = _local(start_raw["dateTime"], tz_offset)
        end = _local(end_raw.get("dateTime", start_raw["dateTime"]), tz_offset)
        all_day = False
    elif "date" in start_raw:
        # All-day events carry a plain date, and their end date is exclusive.
        start = datetime.fromisoformat(start_raw["date"])
        end_date = end_raw.get("date") or start_raw["date"]
        end = datetime.fromisoformat(end_date)
        all_day = True
    else:
        return None  # no usable time; nothing sensible to draw

    return Event(title=(item.get("summary") or "(no title)").strip(),
                 start=start, end=end, all_day=all_day)


def _declined(item: dict) -> bool:
    for attendee in item.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return True
    return False


def _local(stamp: str, tz_offset: int) -> datetime:
    """RFC3339 to a naive local wall clock, matching the rest of the display.

    Python 3.9's fromisoformat cannot read a trailing "Z", which is what the
    API returns for a calendar kept in UTC.
    """
    if stamp.endswith(("Z", "z")):
        stamp = stamp[:-1] + "+00:00"
    parsed = datetime.fromisoformat(stamp)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone(timedelta(seconds=tz_offset))).replace(tzinfo=None)


def _offset(tz_offset: int) -> str:
    """Seconds east of UTC as the "+02:00" that timeMin/timeMax require."""
    sign = "+" if tz_offset >= 0 else "-"
    minutes = abs(tz_offset) // 60
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"
