"""Tests for the advisory rules. No network; forecasts are built by hand.

Run with:  python3 -m unittest discover -s . -v
"""

import unittest
from datetime import date, datetime, timedelta

import advisor
from advisor import DayWindow, summarise
from openmeteo import Daily, Forecast, Slot

DAY = date(2026, 8, 31)
WINDOW = DayWindow(start_hour=6, end_hour=21, commutes=((7, 9), (17, 19)))


def slot(hour, **kw):
    """One hour whose values cover [hour-1, hour]; calm and dry by default."""
    end = datetime(DAY.year, DAY.month, DAY.day) + timedelta(hours=hour)
    base = dict(temp=24.0, feels=24.0, pop=0, precip=0.0, rain=0.0, showers=0.0,
                snow=0.0, code=1, cloud=10, wind=5.0, gust=10.0, uv=1.0,
                surface_temp=None, dew_point=None, snow_depth=None)
    base.update(kw)
    if "precip" not in kw:
        base["precip"] = base["rain"] + base["showers"] + base["snow"] * 10
    return Slot(start=end - timedelta(hours=1), end=end, **base)


def forecast(slots, **daily):
    d = dict(temp_max=28.0, temp_min=20.0, feels_max=30.0, feels_min=21.0,
             precip_hours=0.0, pop_max=0, code=1)
    d.update(daily)
    day = Daily(day=DAY, sunrise=datetime(2026, 8, 31, 5, 43),
                sunset=datetime(2026, 8, 31, 18, 9), **d)
    return Forecast(fetched_at=datetime(2026, 8, 31, 6, 5), today=day, slots=slots)


def full_day(**kw):
    """A quiet 24 hours, so tests only have to state the interesting hours."""
    return [slot(h, **kw) for h in range(1, 25)]


def texts(summary):
    return [a.text for a in summary.advisories]


def kinds(summary):
    return {a.kind for a in summary.advisories}


class TestWindow(unittest.TestCase):
    def test_ignores_rain_outside_the_day_window(self):
        slots = full_day()
        slots[21] = slot(22, rain=12.0, pop=95)  # heavy, 9-10pm, past end_hour
        s = summarise(forecast(slots), WINDOW)
        self.assertNotIn("rain", kinds(s))

    def test_catches_the_same_rain_inside_the_window(self):
        slots = full_day()
        slots[17] = slot(18, rain=12.0, pop=95)  # 5-6pm
        s = summarise(forecast(slots), WINDOW)
        self.assertIn("rain", kinds(s))

    def test_no_slots_in_window(self):
        s = summarise(forecast(full_day()), DayWindow(start_hour=6, end_hour=6))
        self.assertEqual(s.headline, "No forecast for today")


class TestRain(unittest.TestCase):
    def test_heavy_rain_during_commute_advises_umbrella(self):
        slots = full_day()
        for h in (18, 19):  # 5-7pm, evening commute
            slots[h - 1] = slot(h, rain=9.0, pop=93, cloud=100)
        s = summarise(forecast(slots), WINDOW)
        self.assertIn("Heavy rain 5-7pm - bring an umbrella", texts(s))

    def test_midday_moderate_rain_still_advises_umbrella(self):
        slots = full_day()
        slots[12] = slot(13, rain=4.0, pop=80, cloud=95)  # noon-1pm
        s = summarise(forecast(slots), WINDOW)
        self.assertIn("Rain 12-1pm - bring an umbrella", texts(s))

    def test_light_midday_drizzle_is_not_worth_a_line(self):
        slots = full_day()
        slots[13] = slot(14, rain=0.3, pop=30)
        s = summarise(forecast(slots), WINDOW)
        self.assertNotIn("rain", kinds(s))

    def test_a_dry_hour_does_not_split_one_rainy_stretch(self):
        slots = full_day()
        slots[13] = slot(14, rain=3.0, pop=70)
        slots[14] = slot(15, rain=0.0, pop=20)   # brief lull
        slots[15] = slot(16, rain=3.0, pop=70)
        s = summarise(forecast(slots), WINDOW)
        rain = [a for a in s.advisories if a.kind == "rain"]
        self.assertEqual(len(rain), 1)
        self.assertIn("1-4pm", rain[0].text)

    def test_high_chance_of_no_measurable_rain_reads_as_likely(self):
        slots = full_day()
        for h in (8, 9):
            slots[h - 1] = slot(h, rain=0.0, showers=0.0, precip=0.05, pop=75, cloud=90)
        s = summarise(forecast(slots), WINDOW)
        self.assertTrue(any("Rain likely" in t for t in texts(s)), texts(s))


class TestSnowAndStorms(unittest.TestCase):
    def test_snow_is_reported_with_its_hour(self):
        slots = full_day(temp=-2.0, feels=-5.0)
        slots[14] = slot(15, snow=0.4, temp=-2.0, feels=-5.0, pop=90)  # 2-3pm
        s = summarise(forecast(slots, temp_min=-4.0, feels_min=-8.0), WINDOW)
        self.assertTrue(any(t.startswith("Snow 2-3pm") for t in texts(s)), texts(s))

    def test_heavy_snow_outranks_rain(self):
        slots = full_day(temp=-1.0, feels=-4.0)
        slots[14] = slot(15, snow=2.0, temp=-1.0, feels=-4.0, pop=95)
        s = summarise(forecast(slots, temp_min=-3.0, feels_min=-7.0), WINDOW)
        self.assertTrue(any("Heavy snow" in t for t in texts(s)), texts(s))

    def test_thunderstorms_are_flagged(self):
        slots = full_day()
        slots[15] = slot(16, code=95, rain=6.0, pop=90)
        s = summarise(forecast(slots), WINDOW)
        self.assertIn("thunder", kinds(s))


class TestTemperature(unittest.TestCase):
    def test_extreme_heat(self):
        slots = full_day(temp=41.0, feels=44.0, cloud=5)
        s = summarise(forecast(slots, temp_max=41.0, feels_max=44.0), WINDOW)
        self.assertTrue(any("Extreme heat" in t for t in texts(s)), texts(s))

    def test_deep_cold_advises_thick_clothes(self):
        slots = full_day(temp=-10.0, feels=-12.0)
        s = summarise(forecast(slots, temp_max=-8.0, temp_min=-12.0,
                               feels_max=-10.0, feels_min=-14.0), WINDOW)
        self.assertTrue(any("thick layers" in t for t in texts(s)), texts(s))

    def test_mild_day_says_nothing_about_temperature(self):
        s = summarise(forecast(full_day(temp=23.0, feels=24.0)), WINDOW)
        self.assertNotIn("cold", kinds(s))
        self.assertNotIn("heat", kinds(s))


class TestWind(unittest.TestCase):
    def test_strong_gusts_warn_about_umbrellas(self):
        slots = full_day()
        slots[17] = slot(18, gust=72.0, wind=45.0)
        s = summarise(forecast(slots), WINDOW)
        self.assertTrue(any("Strong gusts" in t for t in texts(s)), texts(s))


class TestHeadline(unittest.TestCase):
    def test_clear_day(self):
        self.assertEqual(summarise(forecast(full_day(cloud=2)), WINDOW).headline,
                         "Clear and sunny")

    def test_mostly_sunny(self):
        self.assertEqual(summarise(forecast(full_day(cloud=15)), WINDOW).headline,
                         "Mostly sunny")

    def test_cloudy(self):
        self.assertEqual(summarise(forecast(full_day(cloud=95)), WINDOW).headline,
                         "Cloudy")

    def test_raining_all_day(self):
        s = summarise(forecast(full_day(rain=2.0, pop=90, cloud=100)), WINDOW)
        self.assertEqual(s.headline, "Raining all day")

    def test_rain_on_and_off(self):
        slots = full_day(cloud=80)
        for h in range(10, 17):  # 7 wet hours out of 15
            slots[h - 1] = slot(h, rain=1.0, pop=60, cloud=90)
        self.assertEqual(summarise(forecast(slots), WINDOW).headline, "Rain on and off")

    def test_quiet_day_has_no_advisories(self):
        s = summarise(forecast(full_day(cloud=20, temp=22.0, feels=23.0)), WINDOW)
        self.assertTrue(s.quiet)
        self.assertEqual(s.headline, "Mostly sunny")


class TestSelection(unittest.TestCase):
    def test_at_most_max_advisories_kept_in_time_order(self):
        slots = full_day(temp=39.0, feels=42.0, cloud=5)
        slots[7] = slot(8, rain=9.0, pop=95, temp=30.0, feels=34.0)
        slots[15] = slot(16, code=95, rain=8.0, pop=95, temp=38.0, feels=41.0)
        slots[17] = slot(18, gust=75.0, temp=36.0, feels=40.0)
        s = summarise(forecast(slots, temp_max=39.0, feels_max=42.0), WINDOW, max_advisories=3)
        self.assertEqual(len(s.advisories), 3)
        times = [a.start for a in s.advisories if a.start]
        self.assertEqual(times, sorted(times))

    def test_commute_rain_beats_midday_rain_for_the_last_slot(self):
        slots = full_day()
        slots[12] = slot(13, rain=3.0, pop=70)   # noon-1pm, no commute
        slots[7] = slot(8, rain=3.0, pop=70)     # 7-8am, commute
        s = summarise(forecast(slots), WINDOW, max_advisories=1)
        self.assertIn("umbrella", s.advisories[0].text)
        self.assertEqual(s.advisories[0].start.hour, 7)


class TestSpanFormatting(unittest.TestCase):
    def test_single_hour_reads_as_an_interval(self):
        self.assertEqual(advisor._span([slot(18)]), "5-6pm")

    def test_span_across_noon_repeats_the_meridiem(self):
        self.assertEqual(advisor._span([slot(12), slot(13)]), "11am-1pm")

    def test_morning_span(self):
        self.assertEqual(advisor._span([slot(8), slot(9)]), "7-9am")


class TestOverlappingRules(unittest.TestCase):
    def test_snowy_hours_do_not_also_report_rain(self):
        """Open-Meteo counts snowfall in `precipitation`, so the rain rule has to
        skip frozen hours or a snowy afternoon gets reported twice."""
        slots = full_day(temp=-3.0, feels=-6.0, cloud=80)
        for h in (14, 15, 16):
            slots[h - 1] = slot(h, snow=1.4, precip=1.0, pop=95, code=75,
                                temp=-3.0, feels=-6.0, cloud=90)
        s = summarise(forecast(slots, temp_max=-2.0, temp_min=-8.0,
                               feels_max=-5.0, feels_min=-12.0), WINDOW)
        self.assertIn("snow", kinds(s))
        self.assertNotIn("rain", kinds(s))

    def test_sleet_still_reports_rain(self):
        slots = full_day(temp=1.0, feels=-2.0, cloud=90)
        for h in (14, 15):
            slots[h - 1] = slot(h, snow=0.2, rain=3.0, precip=5.0, pop=90,
                                temp=1.0, feels=-2.0)
        # Raised above the usual three: a sleet day legitimately produces ice,
        # cold and snow advisories that outrank rain, and this is asserting the
        # rain rule fired at all rather than how it ranked.
        s = summarise(forecast(slots, temp_max=2.0, temp_min=-1.0,
                               feels_max=0.0, feels_min=-4.0), WINDOW,
                      max_advisories=8)
        self.assertIn("rain", kinds(s))


class TestUV(unittest.TestCase):
    def test_high_uv_reports_the_whole_stretch(self):
        slots = full_day(cloud=2, uv=1.0)
        for h in range(11, 16):
            slots[h - 1] = slot(h, uv=10.5, cloud=2)
        s = summarise(forecast(slots), WINDOW)
        uv = [a for a in s.advisories if a.kind == "uv"]
        self.assertEqual(len(uv), 1)
        self.assertIn("10am-3pm", uv[0].text)

    def test_moderate_uv_is_not_mentioned(self):
        s = summarise(forecast(full_day(uv=6.0, cloud=20)), WINDOW)
        self.assertNotIn("uv", kinds(s))


class TestUnits(unittest.TestCase):
    def test_imperial_advisories_use_fahrenheit_and_mph(self):
        slots = full_day(temp=-10.0, feels=-12.0)
        slots[17] = slot(18, gust=72.0, temp=-10.0, feels=-12.0)
        s = summarise(forecast(slots, temp_max=-8.0, temp_min=-12.0,
                               feels_max=-10.0, feels_min=-14.0),
                      WINDOW, units=advisor.Units(imperial=True))
        text = " ".join(texts(s))
        self.assertIn("10°", text)      # -12C rounds to 10F
        self.assertIn("mph", text)
        self.assertNotIn("km/h", text)

    def test_metric_is_the_default(self):
        slots = full_day()
        slots[17] = slot(18, gust=72.0)
        s = summarise(forecast(slots), WINDOW)
        self.assertIn("km/h", " ".join(texts(s)))


class TestBlackIce(unittest.TestCase):
    """Open-Meteo has no black-ice variable, so this is derived. The cases that
    must NOT fire matter as much as the ones that must."""

    def test_freezing_rain_is_called_out_explicitly(self):
        slots = full_day(temp=-1.0, feels=-5.0, surface_temp=-1.0, cloud=95)
        for h in (7, 8):  # morning commute
            slots[h - 1] = slot(h, code=66, rain=1.5, precip=1.5, pop=95,
                                temp=-1.0, feels=-5.0, surface_temp=-1.5)
        s = summarise(forecast(slots, temp_max=0.0, temp_min=-3.0,
                               feels_max=-4.0, feels_min=-7.0), WINDOW)
        ice = [a for a in s.advisories if a.kind == "ice"]
        self.assertEqual(len(ice), 1)
        self.assertIn("Freezing rain 6-8am", ice[0].text)
        self.assertIn("black ice", ice[0].text)

    def test_freezing_drizzle_is_named_as_drizzle(self):
        slots = full_day(temp=-1.0, feels=-4.0, surface_temp=-1.0, cloud=95)
        for h in (7, 8):
            slots[h - 1] = slot(h, code=56, rain=0.3, precip=0.3, pop=85,
                                temp=-1.0, feels=-4.0, surface_temp=-1.0)
        s = summarise(forecast(slots, temp_max=0.0, temp_min=-3.0,
                               feels_max=-3.0, feels_min=-6.0), WINDOW)
        self.assertTrue(any("Freezing drizzle" in a.text for a in s.advisories))

    def test_wet_road_that_drops_below_freezing(self):
        """Rain in the afternoon, surface below zero by evening."""
        slots = full_day(temp=3.0, feels=1.0, surface_temp=3.0, cloud=90)
        for h in (13, 14):
            slots[h - 1] = slot(h, rain=2.0, precip=2.0, pop=90,
                                temp=3.0, feels=1.0, surface_temp=2.0)
        for h in (18, 19, 20):
            slots[h - 1] = slot(h, temp=-1.0, feels=-4.0, surface_temp=-1.5, cloud=60)
        s = summarise(forecast(slots, temp_max=4.0, temp_min=-2.0,
                               feels_max=2.0, feels_min=-5.0), WINDOW)
        ice = [a for a in s.advisories if a.kind == "ice"]
        self.assertTrue(ice, [a.text for a in s.advisories])
        self.assertIn("Black ice likely", ice[0].text)
        self.assertEqual(ice[0].start.hour, 17)

    def test_melt_refreeze_over_lying_snow(self):
        """Snow on the ground, a thaw, then back below freezing: the classic."""
        slots = full_day(temp=-3.0, feels=-7.0, surface_temp=-3.0,
                         snow_depth=0.12, cloud=40)
        for h in (12, 13, 14):  # midday thaw
            slots[h - 1] = slot(h, temp=2.0, feels=0.0, surface_temp=1.5,
                                snow_depth=0.10, cloud=30)
        s = summarise(forecast(slots, temp_max=2.0, temp_min=-5.0,
                               feels_max=0.0, feels_min=-9.0), WINDOW)
        self.assertTrue(any(a.kind == "ice" for a in s.advisories),
                        [a.text for a in s.advisories])

    def test_dry_lying_snow_that_never_thawed_is_not_black_ice(self):
        """Deep cold over lying snow is dry. Reporting ice here would be noise."""
        slots = full_day(temp=-15.0, feels=-20.0, surface_temp=-14.0,
                         snow_depth=0.30, dew_point=-18.0, cloud=30)
        s = summarise(forecast(slots, temp_max=-13.0, temp_min=-17.0,
                               feels_max=-18.0, feels_min=-22.0), WINDOW)
        self.assertNotIn("ice", kinds(s))

    def test_frost_on_a_clear_cold_morning_with_no_rain(self):
        """Dew point at or above a sub-zero surface deposits ice directly."""
        slots = full_day(temp=1.0, feels=-1.0, surface_temp=-1.5,
                         dew_point=-0.5, cloud=5)
        s = summarise(forecast(slots, temp_max=6.0, temp_min=0.0,
                               feels_max=4.0, feels_min=-2.0), WINDOW)
        ice = [a for a in s.advisories if a.kind == "ice"]
        self.assertTrue(ice, [a.text for a in s.advisories])
        self.assertIn("Frost", ice[0].text)

    def test_dry_air_over_a_frozen_road_is_not_frost(self):
        slots = full_day(temp=1.0, feels=-1.0, surface_temp=-1.5,
                         dew_point=-9.0, cloud=5)
        s = summarise(forecast(slots, temp_max=6.0, temp_min=0.0,
                               feels_max=4.0, feels_min=-2.0), WINDOW)
        self.assertNotIn("ice", kinds(s))

    def test_a_warm_wet_day_is_never_icy(self):
        slots = full_day(temp=18.0, feels=18.0, surface_temp=19.0,
                         dew_point=15.0, rain=3.0, precip=3.0, pop=90, cloud=95)
        s = summarise(forecast(slots, temp_max=20.0, temp_min=15.0,
                               feels_max=20.0, feels_min=15.0), WINDOW)
        self.assertNotIn("ice", kinds(s))

    def test_air_temperature_stands_in_when_surface_is_unavailable(self):
        """soil_temperature_0cm can be missing; the road still runs colder than air."""
        slots = full_day(temp=1.0, feels=-2.0, surface_temp=None, cloud=90)
        for h in (7, 8, 9):
            slots[h - 1] = slot(h, temp=1.0, feels=-2.0, rain=1.0, precip=1.0,
                                pop=90, surface_temp=None)
        s = summarise(forecast(slots, temp_max=3.0, temp_min=0.0,
                               feels_max=1.0, feels_min=-3.0), WINDOW)
        self.assertIn("ice", kinds(s))

    def test_missing_surface_temperature_is_not_read_as_zero(self):
        """A None surface temp on a hot day must not look like a frozen road."""
        slots = full_day(temp=30.0, feels=33.0, surface_temp=None,
                         dew_point=24.0, rain=2.0, precip=2.0, pop=80, cloud=90)
        s = summarise(forecast(slots, temp_max=32.0, temp_min=26.0,
                               feels_max=35.0, feels_min=28.0), WINDOW)
        self.assertNotIn("ice", kinds(s))

    def test_ice_outranks_rain_and_cold_for_the_single_line(self):
        slots = full_day(temp=-1.0, feels=-6.0, surface_temp=-1.0, cloud=95)
        for h in (7, 8):
            slots[h - 1] = slot(h, code=67, rain=4.0, precip=4.0, pop=95,
                                temp=-1.0, feels=-6.0, surface_temp=-1.0)
        s = summarise(forecast(slots, temp_max=0.0, temp_min=-4.0,
                               feels_max=-5.0, feels_min=-9.0), WINDOW,
                      max_advisories=1)
        self.assertEqual(s.advisories[0].kind, "ice")


if __name__ == "__main__":
    unittest.main()
