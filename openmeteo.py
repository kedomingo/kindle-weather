"""Open-Meteo client: fetch a forecast and normalise it into hourly slots.

No third-party HTTP library; urllib from the stdlib is plenty for one call a
few times an hour.

Everything is requested in metric units so the advisory thresholds in
`advisor.py` can be plain numbers. Conversion happens at render time only.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta

API_URL = "https://api.open-meteo.com/v1/forecast"

HOURLY_VARS = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "uv_index",
    # For deriving black ice: the road surface runs colder than the 2m air, and
    # frost deposits when the surface is below both freezing and the dew point.
    "soil_temperature_0cm",
    "dew_point_2m",
    "snow_depth",
)

DAILY_VARS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "apparent_temperature_max",
    "apparent_temperature_min",
    "precipitation_hours",
    "precipitation_probability_max",
    "weather_code",
    "sunrise",
    "sunset",
)


@dataclass
class Slot:
    """One hour of forecast.

    Open-Meteo reports accumulating variables (precipitation, rain, showers,
    snowfall, precipitation_probability, wind gusts) as the sum/max over the
    *preceding* hour, and instantaneous variables (temperature, cloud cover,
    weather code, wind speed, UV) as the value *at* the timestamp. So for the
    API record stamped 17:00: `start` is 16:00, `end` is 17:00, the rain fell
    somewhere in between, and `temp` is the reading taken at 17:00.
    """

    start: datetime
    end: datetime
    temp: float
    feels: float
    pop: int
    precip: float
    rain: float
    showers: float
    snow: float
    code: int
    cloud: int
    wind: float
    gust: float
    uv: float
    # Optional: these can be absent depending on location and model, and a
    # missing surface temperature must never be read as 0 degrees.
    surface_temp: float | None = None
    dew_point: float | None = None
    snow_depth: float | None = None   # metres, unlike snowfall which is cm

    @property
    def liquid(self) -> float:
        """Liquid precipitation in mm for this hour (rain plus showers)."""
        return self.rain + self.showers


@dataclass
class Daily:
    day: date
    temp_max: float
    temp_min: float
    feels_max: float
    feels_min: float
    precip_hours: float
    pop_max: int
    code: int
    sunrise: datetime
    sunset: datetime


@dataclass
class Forecast:
    fetched_at: datetime
    today: Daily
    slots: list[Slot]

    def window(self, start_hour: int, end_hour: int) -> list[Slot]:
        """Slots whose hour overlaps [start_hour, end_hour) on the forecast day."""
        lo = datetime.combine(self.today.day, datetime.min.time()).replace(hour=start_hour)
        hi = datetime.combine(self.today.day, datetime.min.time()) + timedelta(hours=end_hour)
        return [s for s in self.slots if s.end > lo and s.start < hi]


def build_url(latitude: float, longitude: float, timezone: str) -> str:
    params = {
        "latitude": f"{latitude}",
        "longitude": f"{longitude}",
        "hourly": ",".join(HOURLY_VARS),
        "daily": ",".join(DAILY_VARS),
        "timezone": timezone,
        # Two days so the local day is always fully covered: the record at
        # 00:00 belongs to yesterday's last hour, and hour 23->24 lives in
        # tomorrow's 00:00 record.
        "forecast_days": "2",
    }
    return API_URL + "?" + urllib.parse.urlencode(params)


def fetch_raw(latitude: float, longitude: float, timezone: str, timeout: float = 20.0) -> dict:
    url = build_url(latitude, longitude, timezone)
    req = urllib.request.Request(url, headers={"User-Agent": "weather-display/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse(raw: dict, day: date, fetched_at: datetime) -> Forecast:
    """Turn an Open-Meteo payload into a Forecast for `day` (local date)."""
    hourly = raw["hourly"]
    times = [datetime.fromisoformat(t) for t in hourly["time"]]

    slots: list[Slot] = []
    for i, ts in enumerate(times):
        temp = _opt(hourly, "temperature_2m", i)
        feels = _opt(hourly, "apparent_temperature", i)
        if temp is None or feels is None:
            # Nulls appear at series edges. A slot with no temperature cannot be
            # judged, so drop it rather than let a zero stand in for it.
            continue
        slots.append(
            Slot(
                start=ts - timedelta(hours=1),
                end=ts,
                temp=temp,
                feels=feels,
                pop=int(_num(hourly, "precipitation_probability", i)),
                precip=_num(hourly, "precipitation", i),
                rain=_num(hourly, "rain", i),
                showers=_num(hourly, "showers", i),
                snow=_num(hourly, "snowfall", i),
                code=int(_num(hourly, "weather_code", i)),
                cloud=int(_num(hourly, "cloud_cover", i)),
                wind=_num(hourly, "wind_speed_10m", i),
                gust=_num(hourly, "wind_gusts_10m", i),
                uv=_num(hourly, "uv_index", i),
                surface_temp=_opt(hourly, "soil_temperature_0cm", i),
                dew_point=_opt(hourly, "dew_point_2m", i),
                snow_depth=_opt(hourly, "snow_depth", i),
            )
        )

    daily = raw["daily"]
    try:
        d = daily["time"].index(day.isoformat())
    except ValueError as exc:
        raise ValueError(f"forecast has no daily entry for {day}") from exc

    today = Daily(
        day=day,
        temp_max=_num(daily, "temperature_2m_max", d),
        temp_min=_num(daily, "temperature_2m_min", d),
        feels_max=_num(daily, "apparent_temperature_max", d),
        feels_min=_num(daily, "apparent_temperature_min", d),
        precip_hours=_num(daily, "precipitation_hours", d),
        pop_max=int(_num(daily, "precipitation_probability_max", d)),
        code=int(_num(daily, "weather_code", d)),
        sunrise=datetime.fromisoformat(daily["sunrise"][d]),
        sunset=datetime.fromisoformat(daily["sunset"][d]),
    )
    return Forecast(fetched_at=fetched_at, today=today, slots=slots)


def _num(block: dict, key: str, i: int) -> float:
    """Read an accumulating series value; a null means nothing accumulated."""
    value = _opt(block, key, i)
    return 0.0 if value is None else value


def _opt(block: dict, key: str, i: int) -> float | None:
    """Read a series value that may legitimately be absent.

    Used for anything where zero is a meaningful reading rather than a stand-in
    for "no data" -- temperatures above all.
    """
    series = block.get(key)
    if not series or i >= len(series) or series[i] is None:
        return None
    return float(series[i])
