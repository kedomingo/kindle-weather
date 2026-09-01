"""Turn hourly forecast data into a headline plus a short list of advisories.

Two separate outputs, because they answer different questions:

  headline   what today looks like overall ("Mostly sunny", "Raining all day")
  advisories what to do about it, with times ("Heavy rain 4-7pm - umbrella")

Only hours inside the configured day window are considered at all, so heavy
rain at 10pm never shows up. Hours inside a commute window score higher, so
when several things compete for the two or three lines we have room for, the
ones that land on the way to or from work win.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from openmeteo import Forecast, Slot

# Rain rates in mm/h, following the usual meteorological bands.
RAIN_HEAVY = 7.6
RAIN_MODERATE = 2.5
RAIN_LIGHT = 0.2

SNOW_HEAVY = 1.0  # cm/h

# Advisories scoring below this are dropped; the headline covers the day instead.
MIN_SEVERITY = 30

THUNDER_CODES = {95, 96, 99}
FOG_CODES = {45, 48}

# Freezing drizzle (56 light, 57 dense) and freezing rain (66 light, 67 heavy).
# Open-Meteo has no black-ice variable, so these codes plus the surface
# temperature are what it gets derived from.
FREEZING_CODES = {56, 57, 66, 67}
FREEZING_HEAVY_CODES = {57, 67}

# A road surface at or just below zero with water on it. Slightly above zero
# because both the forecast and the road itself carry more than a degree of
# slack, and ice forms on bridges and shaded bends first.
ICE_SURFACE_C = 0.5
# Roads radiate heat overnight and sit colder than the 2m air temperature, so
# this much is subtracted when no surface temperature is available.
ROAD_AIR_OFFSET_C = 1.0
# How far back to look for water still lying on the road.
WET_ROAD_LOOKBACK_H = 6

COMMUTE_BOOST = 15


@dataclass
class Units:
    """Formats numbers for display. Rule thresholds are always metric; this only
    changes how a value is written out."""

    imperial: bool = False

    def deg(self, celsius: float) -> str:
        value = celsius * 9 / 5 + 32 if self.imperial else celsius
        return f"{value:.0f}°"

    def speed(self, kmh: float) -> str:
        if self.imperial:
            return f"{kmh * 0.621371:.0f} mph"
        return f"{kmh:.0f} km/h"


@dataclass
class Advisory:
    kind: str
    severity: int
    text: str
    start: datetime | None = None
    end: datetime | None = None

    @property
    def sort_time(self) -> tuple[int, float]:
        """Timed advisories first, in chronological order; untimed after."""
        if self.start is None:
            return (1, 0.0)
        return (0, self.start.timestamp())


@dataclass
class Summary:
    headline: str
    advisories: list[Advisory] = field(default_factory=list)

    @property
    def quiet(self) -> bool:
        return not self.advisories


@dataclass
class DayWindow:
    """The hours of the day that actually matter, and which of them are commutes."""

    start_hour: int = 6
    end_hour: int = 21
    commutes: tuple[tuple[int, int], ...] = ((7, 9), (17, 19))

    def is_commute(self, slot: Slot) -> bool:
        for lo, hi in self.commutes:
            if slot.end.hour > lo and slot.start.hour < hi:
                return True
            # A slot ending exactly at midnight has hour 0; commutes never
            # straddle midnight, so no special case is needed beyond this.
        return False


def summarise(forecast: Forecast, window: DayWindow, max_advisories: int = 3,
              units: Units | None = None) -> Summary:
    units = units or Units()
    slots = forecast.window(window.start_hour, window.end_hour)
    if not slots:
        return Summary(headline="No forecast for today")

    candidates: list[Advisory] = []
    candidates += _black_ice(slots, forecast.slots, window)
    candidates += _snow(slots, window)
    candidates += _thunder(slots, window)
    candidates += _rain(slots, window)
    candidates += _temperature(slots, units)
    candidates += _wind(slots, window, units)
    candidates += _fog(slots, window)
    candidates += _uv(slots)

    kept = [a for a in candidates if a.severity >= MIN_SEVERITY]
    kept = _dedupe(kept)
    # Most severe first to decide what fits, then chronological to read.
    kept.sort(key=lambda a: -a.severity)
    kept = kept[:max_advisories]
    kept.sort(key=lambda a: a.sort_time)

    return Summary(headline=_headline(slots), advisories=kept)


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


def _black_ice(slots: list[Slot], all_slots: list[Slot], window: DayWindow) -> list[Advisory]:
    """Black ice, which Open-Meteo does not report directly.

    Three ways it forms, in descending order of certainty:

      1. Freezing rain or drizzle falling now (WMO 56/57/66/67). Unambiguous.
      2. A wet road at or below freezing, including the classic melt-refreeze:
         snow or rain earlier, the surface above zero at some point since, and
         now back below it.
      3. Frost deposition on a below-freezing surface when the air is moist
         enough (dew point at or above the surface temperature), which ices a
         road with no precipitation at all.

    All three share the kind "ice" so only the strongest becomes an advisory;
    the reader's response to any of them is the same.
    """
    out: list[Advisory] = []

    for run in _runs(slots, lambda s: s.code in FREEZING_CODES):
        drizzle = all(s.code in {56, 57} for s in run)
        heavy = any(s.code in FREEZING_HEAVY_CODES for s in run)
        label = "Freezing drizzle" if drizzle else "Freezing rain"
        severity = 100 if heavy else 95
        text = f"{label} {_span(run)} - black ice, roads will be treacherous"
        if any(window.is_commute(s) for s in run):
            severity += COMMUTE_BOOST
        out.append(Advisory("ice", severity, text, run[0].start, run[-1].end))

    wet = _wet_road(all_slots)

    def icy(s: Slot) -> bool:
        return _road_temp(s) <= ICE_SURFACE_C and s.end in wet

    for run in _runs(slots, icy):
        severity = 85 + (COMMUTE_BOOST if any(window.is_commute(s) for s in run) else 0)
        out.append(Advisory("ice", severity,
                            f"Black ice likely {_span(run)} - wet roads below freezing",
                            run[0].start, run[-1].end))

    def frosty(s: Slot) -> bool:
        road = _road_temp(s)
        if road > 0.0 or s.dew_point is None:
            return False
        # Moisture deposits as ice only when the surface is at or below the
        # temperature the air gives up its water at.
        return s.dew_point >= road and s.end not in wet

    for run in _runs(slots, frosty):
        severity = 60 + (COMMUTE_BOOST if any(window.is_commute(s) for s in run) else 0)
        out.append(Advisory("ice", severity,
                            f"Frost {_span(run)} - icy patches on the road",
                            run[0].start, run[-1].end))

    return out


def _road_temp(slot: Slot) -> float:
    """Best estimate of the road surface temperature."""
    if slot.surface_temp is not None:
        return slot.surface_temp
    return slot.temp - ROAD_AIR_OFFSET_C


def _wet_road(all_slots: list[Slot]) -> set:
    """Slot end-times where liquid water is plausibly lying on the road.

    Either it rained recently, or there is snow around that has been through a
    thaw -- lying snow on a road that never rose above freezing is dry, and does
    not ice over.
    """
    wet = set()
    for i, s in enumerate(all_slots):
        back = all_slots[max(0, i - WET_ROAD_LOOKBACK_H) : i + 1]
        if any(b.liquid >= 0.1 for b in back):
            wet.add(s.end)
            continue
        snow_around = (s.snow_depth or 0) > 0 or any(b.snow > 0 for b in back)
        thawed = any(_road_temp(b) > ICE_SURFACE_C for b in back)
        if snow_around and thawed:
            wet.add(s.end)
    return wet


def _snow(slots: list[Slot], window: DayWindow) -> list[Advisory]:
    out = []
    for run in _runs(slots, lambda s: s.snow > 0):
        peak = max(s.snow for s in run)
        commute = any(window.is_commute(s) for s in run)
        if peak >= SNOW_HEAVY:
            text = f"Heavy snow {_span(run)}"
            severity = 90
        else:
            text = f"Snow {_span(run)}"
            severity = 75
        if commute:
            severity += COMMUTE_BOOST
            text += " - allow extra travel time"
        out.append(Advisory("snow", severity, text, run[0].start, run[-1].end))
    return out


def _thunder(slots: list[Slot], window: DayWindow) -> list[Advisory]:
    out = []
    for run in _runs(slots, lambda s: s.code in THUNDER_CODES):
        severity = 85
        text = f"Thunderstorms {_span(run)}"
        if any(window.is_commute(s) for s in run):
            severity += COMMUTE_BOOST
            text += " - stay indoors if you can"
        out.append(Advisory("thunder", severity, text, run[0].start, run[-1].end))
    return out


def _rain(slots: list[Slot], window: DayWindow) -> list[Advisory]:
    """Group wet hours into runs and describe the worst of each run."""
    out = []

    def wet(s: Slot) -> bool:
        if s.snow > 0 and s.liquid < RAIN_LIGHT:
            return False  # frozen; the snow rule owns this hour
        return s.liquid >= RAIN_LIGHT or (s.pop >= 60 and s.precip > 0)

    for run in _runs(slots, wet):
        peak = max(s.liquid for s in run)
        pop = max(s.pop for s in run)
        commute = any(window.is_commute(s) for s in run)

        if peak >= RAIN_HEAVY:
            label, severity = "Heavy rain", 80
        elif peak >= RAIN_MODERATE:
            label, severity = "Rain", 60
        elif peak >= RAIN_LIGHT:
            label, severity = "Light rain", 40
        else:
            label, severity = "Rain likely", 35

        # A high chance of only a little rain is still worth an umbrella, but a
        # trace of rain the models barely believe in is not.
        if pop < 40 and peak < RAIN_MODERATE:
            severity -= 15

        text = f"{label} {_span(run)}"
        if commute:
            severity += COMMUTE_BOOST
            text += " - bring an umbrella"
        elif peak >= RAIN_MODERATE:
            text += " - bring an umbrella"

        out.append(Advisory("rain", severity, text, run[0].start, run[-1].end))
    return out


def _temperature(slots: list[Slot], units: Units) -> list[Advisory]:
    """Heat and cold advice, judged on apparent temperature inside the window."""
    hottest = max(slots, key=lambda s: s.feels)
    coldest = min(slots, key=lambda s: s.feels)
    out = []

    fh = hottest.feels
    if fh >= 40:
        out.append(Advisory("heat", 90, f"Extreme heat, feels {units.deg(fh)} - avoid the midday sun", hottest.end, hottest.end))
    elif fh >= 35:
        out.append(Advisory("heat", 70, f"Very hot, feels {units.deg(fh)} - keep water with you", hottest.end, hottest.end))
    elif fh >= 32:
        out.append(Advisory("heat", 45, f"Hot and humid, feels {units.deg(fh)}", hottest.end, hottest.end))

    fc = coldest.feels
    if fc <= -10:
        out.append(Advisory("cold", 90, f"Bitter cold {_at(coldest)}, feels {units.deg(fc)} - thick layers", coldest.end, coldest.end))
    elif fc <= 0:
        out.append(Advisory("cold", 80, f"Freezing {_at(coldest)}, feels {units.deg(fc)} - wear thick clothes", coldest.end, coldest.end))
    elif fc <= 8:
        out.append(Advisory("cold", 50, f"Cold {_at(coldest)}, feels {units.deg(fc)} - bring a jacket", coldest.end, coldest.end))
    elif fc <= 14:
        out.append(Advisory("cold", 32, f"Cool {_at(coldest)}, feels {units.deg(fc)} - light jacket", coldest.end, coldest.end))

    return out


def _wind(slots: list[Slot], window: DayWindow, units: Units) -> list[Advisory]:
    gusty = max(slots, key=lambda s: s.gust)
    if gusty.gust >= 60:
        severity = 65 + (COMMUTE_BOOST if window.is_commute(gusty) else 0)
        return [Advisory("wind", severity, f"Strong gusts {_at(gusty)}, {units.speed(gusty.gust)} - an umbrella will flip", gusty.start, gusty.end)]
    if gusty.gust >= 40:
        return [Advisory("wind", 40, f"Windy {_at(gusty)}, gusts {units.speed(gusty.gust)}", gusty.start, gusty.end)]
    return []


def _fog(slots: list[Slot], window: DayWindow) -> list[Advisory]:
    out = []
    for run in _runs(slots, lambda s: s.code in FOG_CODES):
        severity = 35 + (COMMUTE_BOOST if any(window.is_commute(s) for s in run) else 0)
        out.append(Advisory("fog", severity, f"Fog {_span(run)} - slower traffic", run[0].start, run[-1].end))
    return out


def _uv(slots: list[Slot]) -> list[Advisory]:
    runs = _runs(slots, lambda s: s.uv >= 9)
    if not runs:
        return []
    run = max(runs, key=lambda r: max(s.uv for s in r))
    return [Advisory("uv", 30, f"Very high UV {_span(run)} - sunscreen", run[0].start, run[-1].end)]


def _dedupe(advisories: list[Advisory]) -> list[Advisory]:
    """Keep only the strongest advisory of each kind, so one rainy day does not
    fill every line with rain."""
    best: dict[str, Advisory] = {}
    for a in advisories:
        current = best.get(a.kind)
        if current is None or a.severity > current.severity:
            best[a.kind] = a
    return list(best.values())


# --------------------------------------------------------------------------
# headline
# --------------------------------------------------------------------------


def _headline(slots: list[Slot]) -> str:
    wet_hours = sum(1 for s in slots if s.liquid >= RAIN_LIGHT)
    snow_hours = sum(1 for s in slots if s.snow > 0)
    cloud = sum(s.cloud for s in slots) / len(slots)
    total = len(slots)

    if snow_hours >= total * 0.5:
        return "Snowing all day"
    if wet_hours >= total * 0.7:
        return "Raining all day"
    if wet_hours >= total * 0.35:
        return "Rain on and off"
    if wet_hours >= 2:
        return "Showers at times"

    if cloud >= 85:
        return "Cloudy"
    if cloud >= 60:
        return "Mostly cloudy"
    if cloud >= 30:
        return "Partly cloudy"
    if cloud >= 10:
        return "Mostly sunny"
    return "Clear and sunny"


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------


def _runs(slots: list[Slot], predicate) -> list[list[Slot]]:
    """Split slots into maximal consecutive runs that satisfy `predicate`.

    A gap of a single dry hour inside a rainy stretch is bridged, so 4pm-7pm
    with a lull at 5pm still reads as one advisory rather than two.
    """
    hits = [i for i, s in enumerate(slots) if predicate(s)]
    if not hits:
        return []
    runs: list[list[int]] = [[hits[0]]]
    for i in hits[1:]:
        if i - runs[-1][-1] <= 2:
            runs[-1].append(i)
        else:
            runs.append([i])
    return [[slots[i] for i in range(r[0], r[-1] + 1)] for r in runs]


def _hour(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}{'am' if dt.hour < 12 else 'pm'}"


def _meridiem(dt: datetime) -> str:
    return "am" if dt.hour < 12 else "pm"


def _span(run: list[Slot]) -> str:
    start, end = run[0].start, run[-1].end
    if _meridiem(start) == _meridiem(end):
        h1 = start.hour % 12 or 12
        return f"{h1}-{_hour(end)}"
    return f"{_hour(start)}-{_hour(end)}"


def _at(slot: Slot) -> str:
    return f"at {_hour(slot.end)}"

