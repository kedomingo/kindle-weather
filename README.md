# weather-display, Orange Pi Zero 3 edition

Generates the Kindle weather image from [Open-Meteo](https://open-meteo.com/),
with the display built around one question: *what do I need to know before I
leave the house?*

Differences from the original `server/`:

| | original | this |
|---|---|---|
| data | Yahoo Weather (YQL, dead since 2019) | Open-Meteo, no API key |
| drawing | SVG template → ImageMagick → pngcrush | Pillow, direct to PNG |
| deps | `imagemagick`, `pngcrush`, `myql`, `dom`, Python 2 | `Pillow`, Python 3.9+ |
| content | current temp, wind, humidity, sunrise/sunset | min/max plus timed advisories |

## No, you don't need ImageMagick

The old pipeline existed only to turn an SVG into a grayscale PNG. Pillow does
that in one step, so `imagemagick`, `pngcrush`, the font-config dance in the
original README, and `template.svg` are all gone.

pngcrush is also unnecessary. The renderer posterises to the 16 grey levels the
Kindle panel can actually display, which is both closer to what you'll see and
very compressible: output lands around **12–20 KB**, comparable to what
pngcrush produced. Verified output format is exactly what `eips -f -g` wants:

```
PNG image data, 600 x 800, 8-bit grayscale, non-interlaced
```

The whole install is `apt install python3-pil fonts-dejavu-core`. On an Orange
Pi Zero 3 that is a few seconds and no compiler.

## What it shows

```
┌──────────────────────────────────────────────────────┐
│ MANILA        31° / 26°                 Mon 31 Aug   │  ← city, range, date
│              feels 34° / 28°       Updated 8:08 PM   │  ← when this was made
├──────────────────────────────────────────────────────┤
│      /\_/\           Rain on and off                 │  ← headline
│     ( o.o )                                          │
│      > ^ <          • Light rain 6am-12pm -          │  ← advisories, in
│                       bring an umbrella              │    time order
│   (a random cat)    • Thunderstorms 8-9am -          │
│                       stay indoors if you can        │
│                     ──────────────────────────       │
│                     9:30am  Standup                  │  ← today's next two
│                     2pm     Design review            │    calendar events
├──────────────────────────────────────────────────────┤
│ CHANCE OF RAIN                                       │
│ ────────────────────────────────────────────────     │
│       ☁                                              │  ← one cloud per
│ ██ ██ ██ ██ ▄▄ ▁▁ ░░ ░░ ░░ ░░ ░░ ░░ ░░ ░░ ░░         │    wet stretch
│ 6a    ▂▂▂  9a    12p       3p      6p   ▂▂▂          │  ← commute hours
└──────────────────────────────────────────────────────┘
```

Bars are the hourly chance of rain. **Solid** means rain is actually forecast;
**hollow** means it's only a probability. The two thick marks on the axis are
your commute windows.

### The cat

The left column holds a random PNG from `cat/`, picked fresh on every render.
It is pure decoration and every failure in it is swallowed — a missing
directory or an unreadable file costs you the cat, never the weather.

Two things happen to each image on the way in. Its transparent margin is
trimmed first, so cats of different shapes (one stretching, one scratching) each
fill the column by their own proportions instead of by the padding in their
square canvas. Then it is flattened onto white *before* dropping to 8-bit grey:
converting RGBA straight to `"L"` reads fully transparent pixels as black and
boxes the cat in.

Drop your own PNGs in `cat/` and they join the rotation. `.gitignore` ignores
generated `*.png` but re-includes `cat/*.png`, so artwork is tracked and output
is not.

Making room for this is what moved the day's high and low up into the header,
which in turn freed the space above the bars for the weather icons. The whole
vertical budget is named at the top of `render.py` (`BODY_LIMIT`, `CHART_RULE`,
`ICON_ROW`, `BAR_TOP` …) so it can be audited in one place rather than chased
through magic numbers.

## The calendar

Under the advisories sit today's next couple of events. Finished ones are
dropped — at 3pm a reminder about the 9am standup is just noise — so the band
empties out as the day goes.

The cheapest way in, and the default, is a **public calendar read as `.ics`**.
Make a secondary calendar in Google Calendar, put on it only the events you
want on the wall, set it to public, and put its ID in `.env`:

```sh
CALENDAR_ID=…@group.calendar.google.com
```

That's the whole setup. No Cloud project, no API key, no OAuth, nothing that
expires. You can paste the `?cid=…` share link Google hands you instead of the
ID — it carries the ID base64'd inside it, and `gcal.py` decodes it.

Everything calendar-shaped lives in `.env` rather than in the JSON config, so
the config file stays safe to commit. `.env` is read from beside `weather.py`
at startup, real environment variables take precedence over it (handy for a
one-off `CALENDAR_ID=… ./weather.py`), and `.gitignore` covers it. Start from
`.env.example`; the JSON config keeps only `max_events`, which is a display
choice rather than a credential.

| variable | for |
|---|---|
| `CALENDAR_ID` | the public calendar's ID, or its `?cid=…` share link |
| `CALENDAR_MAX_EVENTS` | how many events to draw; overrides `max_events` |
| `CALENDAR_API_KEY` | only for the API route below |
| `CALENDAR_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | only for a private calendar |
| `CALENDAR_ICS_URL` | a non-Google `.ics` feed, instead of a calendar ID |

With no `CALENDAR_ID` set, the band is simply not drawn — and `--dry-run` says
so, since an empty band otherwise looks identical to a broken one.

The catch is the obvious one: public means public. Anyone holding that ID can
read every title and time on the calendar, so keep it curated and put nothing on
it you would mind a stranger reading.

The feed arrives unexpanded, so recurrence is worked out locally by `ics.py`:
`DAILY`, `WEEKLY` (with `BYDAY`), `MONTHLY` and `YEARLY`, plus `INTERVAL`,
`COUNT`, `UNTIL`, `WKST`, `EXDATE`, and single instances that were moved or
cancelled.
A rule outside that set shows up only on its first date, which errs towards too
little on the panel rather than towards a wrong date.

Two alternatives, if you need them:

| you want | set in `.env` | cost |
|---|---|---|
| the same public calendar, but Google expands recurrence | `CALENDAR_API_KEY` + `CALENDAR_ID` | a Cloud project and an API key |
| a **private** calendar | `CALENDAR_CLIENT_ID`, `_SECRET`, `_REFRESH_TOKEN` | one-time consent via `gcal_setup.py` |

A static API key only ever reaches public data, so the third row is the only way
to read a private calendar. Watch its consent screen: an **External** app left
in **Testing** is issued refresh tokens that expire after 7 days, and the
symptom is a band that goes blank every week for no reason. Set it to **In
production**.

The API itself is free either way — a million requests a day per project, no
billing account — and a render every half hour uses about 48. (Maps Platform is
the opposite deal, which is where the worry usually comes from: it requires
billing but does accept a static key.)

Whichever route, the calendar is strictly an extra. A Google outage, a bad key
or an expired token is caught, logged to stderr, and falls back to the events
cached from the last successful render; the weather draws regardless.

## The advisory rules

This is the part worth tuning. Two separate outputs:

- **Headline** — what the day looks like overall: `Mostly sunny`, `Cloudy`,
  `Rain on and off`, `Raining all day`, `Snowing all day`.
- **Advisories** — up to three things to prepare for, each with a time. If
  nothing scores highly enough, the headline stands alone and the panel reads
  *"Nothing to prepare for."*

Every rule only looks at hours inside `day_start_hour`–`day_end_hour`, so heavy
rain at 10pm is never mentioned. Hours inside a `commutes` window score +15,
so when several things compete for three lines the ones that land on your way
to or from work win, and they pick up the umbrella advice.

| rule | fires on | example |
|---|---|---|
| ice | freezing rain/drizzle, or a derived black-ice risk (below) | `Freezing rain 6-9am - black ice, roads will be treacherous` |
| snow | any snowfall; heavy at ≥1 cm/h | `Heavy snow 4-6pm - allow extra travel time` |
| thunder | weather code 95/96/99 | `Thunderstorms 8-9am - stay indoors if you can` |
| rain | ≥7.6 mm/h heavy, ≥2.5 moderate, ≥0.2 light | `Heavy rain 5-7pm - bring an umbrella` |
| heat | apparent ≥32 / ≥35 / ≥40 °C | `Extreme heat, feels 43° - avoid the midday sun` |
| cold | apparent ≤14 / ≤8 / ≤0 / ≤-10 °C | `Freezing at 7am, feels -3° - wear thick clothes` |
| wind | gusts ≥40 / ≥60 km/h | `Strong gusts at 5pm, 71 km/h - an umbrella will flip` |
| fog | weather code 45/48 | `Fog 6-8am - slower traffic` |
| uv | UV index ≥9 | `Very high UV 10am-3pm - sunscreen` |

### Black ice

Open-Meteo has **no black-ice, road-ice, or road-surface variable** — I checked
the [docs](https://open-meteo.com/en/docs). It does expose the ingredients, so
the rule derives it three ways, in descending order of certainty:

1. **Freezing rain or drizzle falling now** — WMO codes 66/67 (freezing rain,
   light/heavy) and 56/57 (freezing drizzle, light/dense). Unambiguous, so this
   scores highest of any rule in the file.
2. **A wet road at or below freezing** — `soil_temperature_0cm` (the surface
   temperature, not the 2 m air temperature) at ≤0.5 °C with water on the road.
   This covers the classic melt-refreeze: rain or snow earlier, the surface
   above zero at some point since, then back below it.
3. **Frost deposition** — a sub-zero surface with `dew_point_2m` at or above it,
   which ices a road with no precipitation at all.

Three deliberate choices:

- **Lying snow that never thawed is not black ice.** Deep cold over snow is dry.
  The melt-refreeze test requires the surface to have risen above freezing at
  some point in the preceding six hours, otherwise a whole Nordic winter would
  read as an ice warning every day.
- **The road is colder than the air.** Roads radiate heat overnight, so when
  `soil_temperature_0cm` is unavailable the rule falls back to air temperature
  minus 1 °C rather than using it directly.
- **A missing surface temperature is never read as 0 °C.** Absent values come
  back as `None`, not zero — otherwise a gap in the data would look like a
  frozen road on a 30 °C day. There's a test for exactly that.

The 0.5 °C threshold is deliberately above zero: both the forecast and the road
carry more than a degree of slack, and bridges and shaded bends ice first.

Verified against live data for Ushuaia in winter, which produced *"Black ice
likely 7-9pm - wet roads below freezing"* from a genuine melt-refreeze — 0.1 mm
of rain and 2 cm of lying snow, surface temperature peaking at +5.4 °C at midday
and back to 0.0 °C by 20:00.

All three mechanisms share the kind `ice`, so only the strongest becomes an
advisory: the reader's response to any of them is the same.

### Snow

Handled by its own rule: any `snowfall` gives `Snow 2-3pm`, ≥1 cm/h upgrades to
`Heavy snow`, and a commute overlap appends `- allow extra travel time`. A day
that is more than half snowy gets the headline `Snowing all day`.

The chart distinguishes the two three ways. Rainy hours draw as solid bars and
snowy hours as hatched ones; the caption reads `CHANCE OF RAIN`, `CHANCE OF
SNOW` or `CHANCE OF RAIN OR SNOW` to match; and a small cloud — raining or
snowing — sits above each stretch of precipitation, drawn in code by
`_icon_rain` / `_icon_snow` rather than shipped as image files.

The icons are per *stretch*, not per bar. Fifteen glyphs would out-shout the
bars they annotate, and what you read off this chart is the block ("snow all
afternoon"), not the individual hour. Hours that are only a *chance* of
precipitation get no icon, because there is no forecast type to name. To go
per-bar instead, drop the run-scanning loop in `_precip_icons` and call the
icon for every wet slot — but check it at `ICON_SIZE` first: below about 17 px
a raindrop and a snowflake are the same few pixels on a 16-grey panel.

Three details that took some care:

- **Times are intervals, not instants.** Open-Meteo reports precipitation as
  the sum over the *preceding* hour, so a value stamped `17:00` fell between
  4pm and 5pm. Advisories about accumulating variables are phrased as the
  interval they actually cover (`4-5pm`), while temperature and UV, which are
  instantaneous, are phrased at their timestamp (`at 5pm`).
- **A single dry hour doesn't split a rainy stretch.** 1pm–4pm with a lull at
  2pm reads as one advisory, not two.
- **Snowy hours don't also report rain.** Open-Meteo counts snowfall inside
  `precipitation`, so the rain rule skips frozen hours — otherwise a snowy
  afternoon got reported twice. Sleet (snow *and* liquid) still reports both.
- **`snow_depth` is in metres** while `snowfall` is in centimetres. Easy to mix
  up when tuning thresholds.

Thresholds live at the top of `advisor.py`. They're always metric even when the
display is in Fahrenheit, so you can retune them without thinking about units.

## Install on the Orange Pi Zero 3

```sh
sudo apt install python3-pil python3-dotenv fonts-dejavu-core

sudo useradd --system --home /var/lib/weather-display weather
sudo mkdir -p /opt/weather-display /etc/weather-display /var/lib/weather-display/www
sudo cp *.py /opt/weather-display/
sudo cp config.example.json /etc/weather-display/config.json
sudo chown -R weather:weather /var/lib/weather-display

sudo install -o root -g weather -m 640 .env.example /opt/weather-display/.env

sudoedit /etc/weather-display/config.json    # set your city and coordinates
sudoedit /opt/weather-display/.env           # calendar ID, if you want events
```

`.env` is read from beside `weather.py`, which is `/opt/weather-display` — the
service's `WorkingDirectory`, so it is found either way. Mode `640`,
`root:weather`, because it may hold a refresh token.

Point `output` at the directory the web server serves:

```json
{
  "output": "/var/lib/weather-display/www/weather-script-output.png",
  "cache":  "/var/lib/weather-display/last-forecast.json"
}
```

Then enable the renderer, its timer, and the tiny file server:

```sh
sudo cp systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now weather-display.timer weather-http.service
sudo systemctl start weather-display.service     # render one now
journalctl -u weather-display.service -n 20
```

`weather-http.service` is `python3 -m http.server` bound to the LAN on port
8080 — no nginx needed for one Kindle fetching one PNG. If you already run a
web server, drop that unit and point `output` into its document root instead.

Allow the port if the Pi is running a firewall:

```sh
sudo ufw allow from 192.168.0.0/16 to any port 8080 proto tcp
```

There are two schedules, on two machines, and they are independent: this timer
renders the PNG on the Pi, and a cron job **on the Kindle** fetches and draws it
(see [On the Kindle](#on-the-kindle)). Offsetting the Kindle a couple of minutes
— `2,17,32,47 * * * *` — has it fetch just after a fresh render rather than
racing one.

### Where the image ends up

Wherever `output` points, and nowhere else:

| running | `output` | the PNG lands |
|---|---|---|
| by hand, from the repo | `preview.png` | in whatever directory you ran from |
| on the Pi | `/var/lib/weather-display/www/weather-script-output.png` | served at `http://<pi>:8080/weather-script-output.png` |

A relative path really does follow the working directory, so use an absolute one
on the Pi, where systemd or cron picks the cwd rather than you. Two constraints
on it: the **filename** must stay `weather-script-output.png`, because that is
what the Kindle script asks for, and the **directory** must be the one
`weather-http.service` serves. Missing directories are created.

### If you'd rather use cron

The timer is the better fit on the Pi — it survives reboots, catches up after
downtime, and logs to the journal — but a crontab line works:

```cron
*/15 * * * * /usr/bin/python3 /opt/weather-display/weather.py --config /etc/weather-display/config.json >> /var/log/weather-display.log 2>&1
```

Install it for the `weather` user, not root: `sudo crontab -u weather -e`.

Three things cron will bite you with, all avoided by the line above. Absolute
paths for both the interpreter and the script, because cron's `PATH` is nearly
empty. Redirect output somewhere, or every stderr line (`calendar unavailable`,
and so on) becomes mail you never read. And if you run from a virtualenv, name
its interpreter directly — `/path/to/venv/bin/python3` — rather than trying to
activate anything.

The working directory does not matter: `.env` is read from beside `weather.py`,
and `output` and `cache` should be absolute paths anyway.

## On the Kindle

Unzip `../kindleweatherfiles.zip` to the Kindle root as before, then replace
`/mnt/us/weather/display-weather.sh` with `kindle/display-weather.sh` and set
`HOST` to your Pi. Give the Pi a static DHCP lease so the URL stays valid.

Then give it a cron job — the Kindle has no systemd, so this one really is cron:

```cron
2,17,32,47 * * * * /mnt/us/weather/display-weather.sh
```

Under busybox crond that is `crontab -e`, though on many jailbroken models it is
backed by `/etc/crontab/root`; either way the rootfs is mounted read-only, so
`mntroot rw` first if you are editing outside `/mnt/us`.

The copy here also fixes a typo in the original: its retry branch fetched
`weather-script.output.png` (a dot instead of a dash), so the second attempt
could never display anything.

### Newer Kindles

The zip at `../kindleweatherfiles.zip` is the original 2016 package and has not
been repackaged: its `display-weather.sh` still points at the author's old EC2
address and still carries the retry typo, which is why the instructions above
replace it with `kindle/display-weather.sh`. Its `weather-image-error.png` is
600x800, matching the Kindle 4 / Touch / Paperwhite 1 this was built for.

The mechanism — `eips` to draw, `stop framework` to keep the reader UI off the
screen, cron to repeat — is still how these dashboards are built on current
firmware. Two things will actually stop you:

- **The jailbreak, not the script.** Firmware ceilings move; as of mid-2026
  roughly 5.18.x and below is reachable and later firmware is not, so check
  [kindlemodding.org](https://kindlemodding.org/jailbreaking/jailbreak-faq.html)
  against your model's exact version *before* you let it update.
- **Panel size.** `eips` draws at native pixel size from the top-left corner,
  so a 600x800 image on a 1236x1648 Paperwhite 5 is a small picture in the
  corner. Set `width`/`height` to the panel's landscape dimensions.

That second one is only half solved. The layout stretches horizontally, but its
vertical geometry is fixed for a 600px-tall canvas: rendered at 1648x1236 the
ink stops at y=593 and the bottom half of the screen stays white. Making it
scale means turning the constants at the top of `render.py` into fractions of
the height. Common panels, landscape:

| model | `width`, `height` |
|---|---|
| Kindle 4 / Touch / Paperwhite 1 | `800`, `600` |
| Paperwhite 3 / 4, Oasis 2 | `1448`, `1072` |
| Paperwhite 5 / Colorsoft, Oasis 3 | `1648`, `1236` |
| Scribe | `2480`, `1860` |

## Configuration

All keys are optional; defaults are in `DEFAULTS` at the top of `weather.py`.

| key | default | meaning |
|---|---|---|
| `city` | `Manila` | shown in the header |
| `latitude`, `longitude` | Manila | forecast location |
| `timezone` | `Asia/Manila` | an [IANA name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones); all times shown are local to this |
| `output` | `weather-script-output.png` | where the PNG is written, atomically |
| `cache` | `last-forecast.json` | last good payload, for offline fallback |
| `display_units` | `metric` | `imperial` switches to °F and mph |
| `width`, `height` | `800`, `600` | canvas before rotation; see [Newer Kindles](#newer-kindles) before changing |
| `rotate` | `90` | degrees clockwise; `0` gives the landscape original |
| `day_start_hour`, `day_end_hour` | `6`, `21` | hours that can produce an advisory |
| `commutes` | `[[7,9],[17,19]]` | hour ranges that score higher |
| `max_advisories` | `3` | lines of advice |
| `greys` | `16` | grey levels; `0` disables posterising |
| `cat_dir` | `./cat` | a random `.png` from here fills the left column; `null` for none |
| `calendar` | `null` | `{"max_events": 2}`; the ID itself goes in `.env` — see [The calendar](#the-calendar) |
| `fonts` | DejaVu, auto-detected | `{"regular": "...", "bold": "..."}` |

## Running it by hand

```sh
./weather.py --dry-run                    # print the summary, draw nothing
./weather.py --config config.json         # normal run
./weather.py --output /tmp/preview.png    # override the destination
./weather.py --fixture saved.json         # render a saved API payload
```

`--dry-run` needs no Pillow, which makes it a quick way to sanity-check
thresholds over SSH:

```
Manila  Mon 31 Aug 20:07
  high 31°  low 26°
  Rain on and off
  - Light rain 6am-12pm - bring an umbrella   (rain, severity 55)
  - Thunderstorms 8-9am - stay indoors if you can   (thunder, severity 100)
  - Strong gusts at 3pm, 71 km/h - an umbrella will flip   (wind, severity 65)
```

## When the network is down

A failed fetch retries three times with backoff, then falls back to the cached
payload and marks the header `Offline - data from 8:08 PM`, so a flaky wifi
moment leaves a slightly stale display rather than a blank one. If there is no
usable cache, or the cache is too old to cover today, the script exits non-zero
and leaves the previous image in place — the Kindle keeps showing the last good
render. The PNG is written to a temp file and renamed into place, so the Kindle
can never fetch a half-written image.

## Tests

The advisory rules and the iCalendar reader are the parts most worth testing,
and both are pure functions over hand-built input, so the suite needs no network
and no Pillow:

```sh
python3 -m unittest discover -s . -v     # 74 tests
```

## Files

| file | |
|---|---|
| `weather.py` | entry point: config, fetch, cache, atomic write |
| `openmeteo.py` | API call and normalisation into hourly slots |
| `advisor.py` | headline and advisory rules — tune here |
| `gcal.py` | calendar events: public `.ics`, API key, or OAuth |
| `ics.py` | just enough RFC 5545 to expand recurrence locally |
| `gcal_setup.py` | one-time OAuth consent, only for a private calendar |
| `render.py` | Pillow drawing |
| `test_advisor.py` | rule tests |
| `test_ics.py` | iCalendar parsing and recurrence tests |
| `.env.example` | template for the calendar ID and any credentials |
| `cat/` | decorative cat PNGs, one picked at random per render |
| `systemd/` | render timer and LAN file server |
| `kindle/` | client-side fetch and display script |
