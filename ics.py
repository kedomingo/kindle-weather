"""A small iCalendar reader: enough of RFC 5545 to answer "what is on today?"

Google serves a public calendar as .ics to anyone who asks -- no API key, no
Cloud project, no OAuth, nothing that expires -- which makes this the cheapest
way to get events onto the display. The price is that the server does none of
the work the JSON API would do for you: the whole calendar arrives at once and
recurrence rules arrive unexpanded, so the handful of RFC 5545 that matters is
implemented below.

What is deliberately not implemented: BYMONTHDAY, BYSETPOS, and the rest of the
rule vocabulary that a hand-curated display calendar will never use. An event
whose rule is not understood shows up only on its first date, which errs towards
too little on the panel rather than towards a wrong date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# A stepping cap, so a malformed UNTIL cannot spin forever. 4000 steps covers a
# decade of daily events, and any rule that has not reached today by then is not
# one this display needs to draw.
MAX_STEPS = 4000

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def parse(text: str, day: date, tz_offset: int) -> list[dict]:
    """Every occurrence that touches the local `day`, soonest first.

    Returns plain dicts ({title, start, end, all_day}) rather than Events so the
    parser stays independent of the render side.
    """
    masters, overrides, cancelled = [], {}, set()
    for block in _blocks(text):
        item = _vevent(block, tz_offset)
        if item is None:
            continue
        key = (item["uid"], item["recurrence_id"])
        if item["cancelled"]:
            cancelled.add(key)
        elif item["recurrence_id"] is not None:
            overrides[key] = item
        else:
            masters.append(item)

    found = []
    for master in masters:
        for start in _occurrences(master, day):
            key = (master["uid"], start.date())
            if key in cancelled or start.date() in master["exdates"]:
                continue
            # A single edited instance of a recurring event arrives as its own
            # VEVENT; it replaces the generated one, and may have been moved to
            # a different time (still today) or a different day (so, dropped).
            item = overrides.get(key, master)
            if item is not master:
                start = item["start"]
                if start.date() != day:
                    continue
            found.append({
                "title": item["summary"],
                "start": start,
                "end": start + item["duration"],
                "all_day": item["all_day"],
            })

    found.sort(key=lambda e: (e["start"], e["title"]))
    return found


def _blocks(text: str) -> list[list[str]]:
    """VEVENT bodies, with folded lines rejoined.

    RFC 5545 wraps long lines by inserting CRLF plus one space, which lands
    mid-word in exactly the properties worth reading (a long SUMMARY).
    """
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)

    blocks, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = []
        elif line == "END:VEVENT":
            if current is not None:
                blocks.append(current)
            current = None
        elif current is not None:
            current.append(line)
    return blocks


def _vevent(block: list[str], tz_offset: int) -> dict | None:
    props: dict[str, tuple[dict, str]] = {}
    exdates: set[date] = set()
    for line in block:
        name, params, value = _property(line)
        if name is None:
            continue
        if name == "EXDATE":
            for part in value.split(","):
                moment, _ = _datetime(part, params, tz_offset)
                if moment:
                    exdates.add(moment.date())
        else:
            props[name] = (params, value)

    if "DTSTART" not in props:
        return None
    start, all_day = _datetime(props["DTSTART"][1], props["DTSTART"][0], tz_offset)
    if start is None:
        return None

    # Duration, not end time, because that is what survives being moved to
    # another day by a recurrence.
    duration = timedelta(hours=1)
    if "DTEND" in props:
        end, _ = _datetime(props["DTEND"][1], props["DTEND"][0], tz_offset)
        if end and end > start:
            duration = end - start
    elif all_day:
        duration = timedelta(days=1)

    rid = None
    if "RECURRENCE-ID" in props:
        moment, _ = _datetime(props["RECURRENCE-ID"][1], props["RECURRENCE-ID"][0], tz_offset)
        rid = moment.date() if moment else None

    return {
        "uid": props.get("UID", ({}, ""))[1],
        "summary": _unescape(props.get("SUMMARY", ({}, ""))[1]) or "(no title)",
        "start": start,
        "all_day": all_day,
        "duration": duration,
        "rrule": _rrule(props["RRULE"][1]) if "RRULE" in props else None,
        "exdates": exdates,
        "recurrence_id": rid,
        "cancelled": props.get("STATUS", ({}, ""))[1] == "CANCELLED",
    }


def _property(line: str) -> tuple[str | None, dict, str]:
    head, _, value = line.partition(":")
    if not _:
        return None, {}, ""
    name, *rest = head.split(";")
    params = {}
    for param in rest:
        key, _, val = param.partition("=")
        params[key.upper()] = val.strip('"')
    return name.upper(), params, value


def _datetime(value: str, params: dict, tz_offset: int) -> tuple[datetime | None, bool]:
    """One DATE or DATE-TIME as a naive local wall clock.

    Google writes timed events in UTC, which is the only case that needs
    converting. A TZID'd or floating time is taken at face value: the calendar's
    zone and the display's zone are the same zone in every setup this runs in,
    and guessing at a zone database from a two-line parser would be worse.
    """
    value = value.strip()
    try:
        if params.get("VALUE") == "DATE" or len(value) == 8:
            return datetime.strptime(value, "%Y%m%d"), True
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ") + timedelta(seconds=tz_offset), False
        return datetime.strptime(value, "%Y%m%dT%H%M%S"), False
    except ValueError:
        return None, False


def _rrule(value: str) -> dict:
    rule = {}
    for part in value.split(";"):
        key, _, val = part.partition("=")
        rule[key.upper()] = val
    return rule


def _occurrences(item: dict, day: date) -> list[datetime]:
    """The occurrence starts of one VEVENT that land on `day`."""
    start = item["start"]
    rule = item["rrule"]
    if rule is None:
        # A plain event, including one running across midnight into today.
        if start.date() == day or (start.date() < day < (start + item["duration"]).date()
                                   or start < datetime.combine(day, datetime.min.time()) < start + item["duration"]):
            return [start]
        return []

    freq = rule.get("FREQ", "").upper()
    interval = max(1, _int(rule.get("INTERVAL"), 1))
    count = _int(rule.get("COUNT"), None)
    until = None
    if "UNTIL" in rule:
        moment, _ = _datetime(rule["UNTIL"].rstrip("Z"), {}, 0)
        until = moment.date() if moment else None

    clock = start.time()
    emitted = 0
    for occurrence in _step(start.date(), freq, interval, rule):
        if occurrence > day or (until and occurrence > until):
            break
        emitted += 1
        if count is not None and emitted > count:
            break
        if occurrence == day:
            return [datetime.combine(occurrence, clock)]
    return []


def _step(first: date, freq: str, interval: int, rule: dict):
    """Occurrence dates from `first` onwards, oldest first."""
    if freq == "WEEKLY":
        # WKST decides where a week starts, which changes which week a day falls
        # in -- and so, once INTERVAL > 1, whether that day is in a counted week
        # at all. It only bites when BYDAY spans the week boundary (SU,TU under
        # WKST=SU is one week; under WKST=MO the Sunday is the week before), but
        # Google sets WKST on every rule it writes, so honour it.
        wkst = WEEKDAYS.get(rule.get("WKST", "MO").upper(), 0)
        days = {WEEKDAYS[code[-2:]] for code in rule.get("BYDAY", "").split(",")
                if code[-2:] in WEEKDAYS} or {first.weekday()}
        offsets = sorted((weekday - wkst) % 7 for weekday in days)
        week = first - timedelta(days=(first.weekday() - wkst) % 7)
        for n in range(MAX_STEPS):
            base = week + timedelta(weeks=n * interval)
            for offset in offsets:
                moment = base + timedelta(days=offset)
                if moment >= first:
                    yield moment
        return

    if freq == "DAILY":
        for n in range(MAX_STEPS):
            yield first + timedelta(days=n * interval)
        return

    if freq in ("MONTHLY", "YEARLY"):
        months = interval * (12 if freq == "YEARLY" else 1)
        for n in range(MAX_STEPS):
            total = (first.month - 1) + n * months
            year, month = first.year + total // 12, total % 12 + 1
            try:
                yield date(year, month, first.day)
            except ValueError:
                continue  # e.g. the 31st in a 30-day month: skipped, per RFC.
        return

    yield first  # An unrecognised FREQ degrades to a one-off.


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        char = value[i]
        if char == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append({"n": " ", "N": " "}.get(nxt, nxt))
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out).strip()
