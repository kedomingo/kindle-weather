"""Draw the display straight to an 8-bit grayscale PNG with Pillow.

This replaces the SVG template plus ImageMagick plus pngcrush pipeline. The
Kindle's `eips -f -g` wants a grayscale PNG at the panel's native size, which
Pillow produces directly: mode "L", posterised to the 16 grey levels the panel
can actually show (which also makes the PNG small enough that pngcrush buys
nothing).

The canvas is drawn in landscape and rotated at the end, so all coordinates
below read the way the display looks on the wall.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

from advisor import DayWindow, Summary
from gcal import Event
from openmeteo import Forecast, Slot

WHITE = 255
BLACK = 0
MID = 96
GREY = 128
LIGHT = 210
FAINT = 235

MARGIN = 36

# The left column, freed up by moving the day's range into the header.
CAT_BOX = (MARGIN + 4, 138, 292, 400)

# The chart's vertical budget. Moving the temperatures into the header let the
# body end 20px earlier, which buys the icon row above the bars; the bars grew
# from 82px to 88px with what was left. Nothing below BAR_BASE moved, because
# the "commute" caption was already 3px off the bottom edge.
BODY_TOP, BODY_LIMIT = 128, 404

# Calendar events get a fixed band at the foot of the right column rather than
# flowing after the advisories, so they cannot be pushed off the panel by a bad
# weather day. The advisories give up the room instead -- see _summary.
EVENTS_TOP, EVENT_ROW = 336, 26
CHART_CAPTION, CHART_RULE = 418, 440
ICON_ROW, ICON_SIZE = 446, 24
BAR_TOP, BAR_BASE = 472, 558

FONT_CANDIDATES = {
    "regular": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/Library/Fonts/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ),
    "bold": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/Library/Fonts/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ),
}


class Fonts:
    """Lazily loaded, size-cached font set."""

    def __init__(self, regular: str | None = None, bold: str | None = None):
        self.paths = {
            "regular": regular or _first_existing(FONT_CANDIDATES["regular"], "regular"),
            "bold": bold or _first_existing(FONT_CANDIDATES["bold"], "bold"),
        }
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, style: str, size: int) -> ImageFont.FreeTypeFont:
        key = (style, size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self.paths[style], size)
        return self._cache[key]


def _first_existing(paths: tuple[str, ...], style: str) -> str:
    for p in paths:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(
        f"no {style} font found; install DejaVu (apt install fonts-dejavu-core) "
        f'or set "fonts" in the config'
    )


@dataclass
class RenderOptions:
    city: str
    window: DayWindow
    width: int = 800
    height: int = 600
    rotate: int = 90          # degrees clockwise, to match how the Kindle hangs
    imperial: bool = False
    stale: bool = False       # drawing from cache because the fetch failed
    greys: int = 16
    font_regular: str | None = None
    font_bold: str | None = None
    cat_dir: str | None = None   # a random .png from here fills the left column
    cat_pick: str | None = None  # or force one, for previewing
    events: tuple[Event, ...] = ()


def render(forecast: Forecast, summary: Summary, opts: RenderOptions, now: datetime) -> Image.Image:
    fonts = Fonts(opts.font_regular, opts.font_bold)
    img = Image.new("L", (opts.width, opts.height), WHITE)
    d = ImageDraw.Draw(img)

    right = opts.width - MARGIN
    _header(d, fonts, opts, forecast, now, right)
    _cat(img, opts)
    _summary(d, fonts, opts, summary, right, opts.events)
    _events(d, fonts, opts, right)
    _chart(d, fonts, opts, forecast, right)

    if opts.greys:
        img = _posterise(img, opts.greys)
    if opts.rotate:
        img = img.rotate(-opts.rotate, expand=True)
    return img


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------


def _header(d, fonts, opts, forecast, now, right) -> None:
    city = opts.city.upper()
    city_font = fonts.get("bold", 56)
    d.text((MARGIN, 78), city, font=city_font, fill=BLACK, anchor="ls")

    # (c) the date now
    day = forecast.today.day
    date_text = f"{day.strftime('%a')} {day.day} {day.strftime('%b')}"
    date_font = fonts.get("regular", 40)
    d.text((right, 62), date_text, font=date_font, fill=BLACK, anchor="rs")

    # (b) when this image was generated
    stamp = f"Updated {_clock(forecast.fetched_at)}"
    if opts.stale:
        stamp = f"Offline - data from {_clock(forecast.fetched_at)}"
    stamp_font = fonts.get("regular", 19)
    d.text((right, 92), stamp, font=stamp_font, fill=GREY, anchor="rs")

    # (d) the day's range, centred in whatever the city and the date leave free
    _temp_range(
        d, fonts, opts, forecast,
        MARGIN + d.textlength(city, font=city_font) + 30,
        right - max(d.textlength(date_text, font=date_font),
                    d.textlength(stamp, font=stamp_font)) - 30,
    )

    d.line((MARGIN, 110, right, 110), fill=BLACK, width=3)


def _temp_range(d, fonts, opts, forecast, x0: float, x1: float) -> None:
    """(d) high and low on one line, sized to fit the gap it is given.

    The type steps down rather than wrapping or overlapping, because the space
    left over depends on how long the city name is and that is the user's to
    choose.
    """
    day = forecast.today
    hi = f"{_temp(day.temp_max, opts)}°"
    lo = f"{_temp(day.temp_min, opts)}°"
    # Always drawn, at whatever size fits: a range that is a little tight beats
    # one that has silently vanished.
    room = x1 - x0
    for size in (46, 42, 38, 34, 30, 26, 22):
        big = fonts.get("bold", size)
        thin = fonts.get("regular", size)
        gap = size * 0.22
        hi_w, lo_w = d.textlength(hi, font=big), d.textlength(lo, font=big)
        sl_w = d.textlength("/", font=thin)
        width = hi_w + sl_w + lo_w + 2 * gap
        if width <= room:
            break

    x = x0 + (room - width) / 2
    d.text((x, 74), hi, font=big, fill=BLACK, anchor="ls")
    x += hi_w + gap
    d.text((x, 74), "/", font=thin, fill=LIGHT, anchor="ls")
    x += sl_w + gap
    d.text((x, 74), lo, font=big, fill=MID, anchor="ls")

    feels = f"feels {_temp(day.feels_max, opts)}° / {_temp(day.feels_min, opts)}°"
    small = fonts.get("regular", 17)
    if d.textlength(feels, font=small) <= room:
        d.text((x0 + room / 2, 99), feels, font=small, fill=GREY, anchor="ms")


def _cat(img: Image.Image, opts: RenderOptions) -> None:
    """A random cat in the column the temperatures used to occupy.

    Pure decoration, so every failure here is swallowed: a missing directory or
    an unreadable PNG must never cost the display its weather.
    """
    path = _pick_cat(opts)
    if path is None:
        return
    try:
        src = Image.open(path).convert("RGBA")
    except OSError:
        return

    # Trim the transparent margin so each cat fills the column by its own
    # shape; these icons range from tall (a cat scratching) to wide (a cat
    # stretching) inside identical square canvases.
    box = src.getchannel("A").getbbox()
    if box:
        src = src.crop(box)

    # Flatten onto white before dropping to "L": converting RGBA directly would
    # read fully transparent pixels as black and box the cat in.
    flat = Image.new("RGBA", src.size, (255, 255, 255, 255))
    flat.alpha_composite(src)
    grey = flat.convert("L")

    avail_w, avail_h = CAT_BOX[2] - CAT_BOX[0], CAT_BOX[3] - CAT_BOX[1]
    scale = min(avail_w / grey.width, avail_h / grey.height)
    size = (max(1, round(grey.width * scale)), max(1, round(grey.height * scale)))
    grey = grey.resize(size, Image.LANCZOS)
    img.paste(grey, (CAT_BOX[0] + (avail_w - size[0]) // 2,
                     CAT_BOX[1] + (avail_h - size[1]) // 2))


def _pick_cat(opts: RenderOptions) -> str | None:
    if opts.cat_pick:
        return opts.cat_pick if os.path.isfile(opts.cat_pick) else None
    if not opts.cat_dir or not os.path.isdir(opts.cat_dir):
        return None
    files = sorted(f for f in os.listdir(opts.cat_dir) if f.lower().endswith(".png"))
    return os.path.join(opts.cat_dir, random.choice(files)) if files else None


def _summary(d, fonts, opts, summary: Summary, right, events=()) -> None:
    """(e) the headline plus what to prepare for, in the right column.

    When there are events to show, the advisories stop above their band. That
    costs about one advisory on a busy day, which is the right way round: the
    third-ranked advisory is by construction the least urgent thing on screen,
    and an event you have to be somewhere for is not negotiable.
    """
    x = 330
    width = right - x
    y = BODY_TOP
    limit = EVENTS_TOP - 12 if events else BODY_LIMIT

    head = fonts.get("bold", 40)
    for line in _wrap(d, summary.headline, head, width, max_lines=2):
        d.text((x, y), line, font=head, fill=BLACK, anchor="la")
        y += 48

    y += 14
    body = fonts.get("regular", 23)
    if summary.quiet:
        d.text((x, y), "Nothing to prepare for.", font=body, fill=GREY, anchor="la")
        return

    for advisory in summary.advisories:
        lines = _wrap(d, advisory.text, body, width - 22, max_lines=2)
        if y + len(lines) * 29 > limit:
            break
        d.ellipse((x + 2, y + 11, x + 10, y + 19), fill=BLACK)
        for i, line in enumerate(lines):
            d.text((x + 22, y), line, font=body, fill=BLACK, anchor="la")
            y += 29
        y += 8


def _events(d, fonts, opts, right) -> None:
    """The next couple of things on the calendar, under the advisories."""
    if not opts.events:
        return

    d.line((330, EVENTS_TOP, right, EVENTS_TOP), fill=LIGHT, width=2)
    when_font = fonts.get("bold", 18)
    what_font = fonts.get("regular", 21)

    # One column for the times so the titles line up, widened to whatever the
    # longest time actually needs -- "all day" is a lot wider than "9am".
    labels = [_event_time(e) for e in opts.events]
    when_w = max(d.textlength(t, font=when_font) for t in labels)

    y = EVENTS_TOP + 10
    for event, label in zip(opts.events, labels):
        d.text((330, y), label, font=when_font, fill=BLACK, anchor="la")
        title = _ellipsize(d, event.title, what_font, right - (330 + when_w + 14))
        d.text((330 + when_w + 14, y - 1), title, font=what_font, fill=GREY, anchor="la")
        y += EVENT_ROW


def _event_time(event: Event) -> str:
    if event.all_day:
        return "all day"
    hour = event.start.hour % 12 or 12
    suffix = "am" if event.start.hour < 12 else "pm"
    if event.start.minute:
        return f"{hour}:{event.start.minute:02d}{suffix}"
    return f"{hour}{suffix}"


def _ellipsize(d, text: str, font, width: float) -> str:
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "...", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "..."


def _chart(d, fonts, opts, forecast, right) -> None:
    """Chance of rain, hour by hour, across the window that matters."""
    slots = forecast.window(opts.window.start_hour, opts.window.end_hour)
    if not slots:
        return

    top, base = BAR_TOP, BAR_BASE
    span = base - top
    d.text((MARGIN, CHART_CAPTION), _precip_caption(slots),
           font=fonts.get("bold", 15), fill=GREY, anchor="la")
    d.line((MARGIN, CHART_RULE, right, CHART_RULE), fill=LIGHT, width=2)

    n = len(slots)
    slot_w = (right - MARGIN) / n
    bar_w = max(6, slot_w * 0.66)

    # Shade the commute hours, and thicken the axis under them, so they read
    # even where a tall black bar covers the shading.
    commute_runs = []
    for i, s in enumerate(slots):
        if opts.window.is_commute(s):
            x0 = MARGIN + i * slot_w
            d.rectangle((x0, top - 6, x0 + slot_w, base), fill=FAINT)
            if commute_runs and commute_runs[-1][1] == i - 1:
                commute_runs[-1][1] = i
            else:
                commute_runs.append([i, i])

    _precip_icons(d, slots, slot_w)
    d.line((MARGIN, top + span / 2, right, top + span / 2), fill=LIGHT, width=1)

    small = fonts.get("regular", 14)
    for i, s in enumerate(slots):
        cx = MARGIN + i * slot_w + slot_w / 2
        h = span * min(s.pop, 100) / 100
        style = _bar_style(s)
        if h >= 1:
            box = (cx - bar_w / 2, base - h, cx + bar_w / 2, base)
            if style == "rain":
                d.rectangle(box, fill=BLACK)
            elif style == "snow":
                # Hatched, so a snowy hour is distinguishable from a rainy one
                # at a glance rather than only from the caption.
                d.rectangle(box, fill=WHITE, outline=BLACK, width=2)
                _hatch(d, box, spacing=4, fill=BLACK, width=1)
            else:
                d.rectangle(box, fill=WHITE, outline=GREY, width=2)
        if s.pop >= 50:
            # Tall bars carry their number inside, so a 99% bar cannot push its
            # label up into the caption.
            if h >= 26:
                if style == "rain":
                    d.text((cx, base - h + 4), f"{s.pop}", font=small, fill=WHITE, anchor="ma")
                else:
                    # Clear a patch first: black on hatching is unreadable.
                    d.rectangle((cx - bar_w / 2 + 2, base - h + 2,
                                 cx + bar_w / 2 - 2, base - h + 19), fill=WHITE)
                    d.text((cx, base - h + 4), f"{s.pop}", font=small, fill=BLACK, anchor="ma")
            else:
                d.text((cx, base - h - 6), f"{s.pop}", font=small, fill=BLACK, anchor="ms")
        if s.start.hour % 3 == 0:
            d.text((cx, base + 4), _short_hour(s.start), font=small, fill=GREY, anchor="ma")

    d.line((MARGIN, base, right, base), fill=BLACK, width=2)
    for lo, hi in commute_runs:
        d.rectangle((MARGIN + lo * slot_w, base, MARGIN + (hi + 1) * slot_w, base + 5), fill=BLACK)
    if commute_runs:
        lo = commute_runs[0][0]
        d.text((MARGIN + lo * slot_w, base + 22), "commute", font=small, fill=GREY, anchor="la")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _precip_icons(d, slots, slot_w: float) -> None:
    """A cloud above each stretch of precipitation, centred over the stretch.

    Per stretch rather than per bar: fifteen glyphs would out-shout the bars
    they annotate, and what you read off this chart is the block ("snow all
    afternoon"), not the individual hour. Hours that are only a *chance* of
    precipitation get no icon, which is the honest thing to draw - there is no
    forecast type to name.
    """
    icons = {"rain": _icon_rain, "snow": _icon_snow}
    i = 0
    while i < len(slots):
        style = _bar_style(slots[i])
        j = i
        while j + 1 < len(slots) and _bar_style(slots[j + 1]) == style:
            j += 1
        if style in icons:
            cx = MARGIN + (i + j + 1) / 2 * slot_w
            icons[style](d, cx, ICON_ROW, ICON_SIZE)
        i = j + 1


def _bar_style(slot: Slot) -> str:
    """Which of the three bar styles this hour gets.

    Liquid wins over snow for sleety hours: an umbrella is the response either
    way, and that is what the solid bar signals.
    """
    if slot.liquid >= 0.2:
        return "rain"
    if slot.snow > 0:
        return "snow"
    return "chance"


def _precip_caption(slots: list[Slot]) -> str:
    """Label the chart for what is actually forecast, not always "rain".

    The bar heights are Open-Meteo's precipitation_probability, which is the
    chance of more than 0.1 mm of precipitation *of any kind* -- snow included,
    counted as its liquid-water equivalent. So only the wording needs to change.

    """
    snowy = any(s.snow > 0 for s in slots)
    rainy = any(s.liquid >= 0.2 for s in slots)

    if snowy and rainy:
        return "CHANCE OF RAIN OR SNOW"
    if snowy:
        return "CHANCE OF SNOW"
    if rainy:
        return "CHANCE OF RAIN"

    # Nothing forecast, so the bars are bare probability. Word it for what would
    # fall if it did: below freezing that is snow, not rain.
    warmest = max(s.temp for s in slots)
    coldest = min(s.temp for s in slots)
    if warmest <= 1.0:
        return "CHANCE OF SNOW"
    if coldest <= 1.0:
        return "CHANCE OF RAIN OR SNOW"
    return "CHANCE OF RAIN"


def _cloud(d, cx: float, cy: float, w: float) -> None:
    """A small filled cloud, `w` wide, its body centred on (cx, cy)."""
    r = w * 0.27
    d.ellipse((cx - w / 2, cy - r, cx - w / 2 + 2 * r, cy + r), fill=BLACK)
    d.ellipse((cx + w / 2 - 2 * r, cy - r, cx + w / 2, cy + r), fill=BLACK)
    d.ellipse((cx - r * 1.15, cy - r * 1.6, cx + r * 1.15, cy + r * 0.7), fill=BLACK)
    d.rectangle((cx - w / 2, cy - r * 0.2, cx + w / 2, cy + r), fill=BLACK)


def _icon_rain(d, cx: float, top: float, size: float) -> None:
    _cloud(d, cx, top + size * 0.32, size)
    for dx in (-0.24, 0.0, 0.24):
        x = cx + dx * size
        d.line((x, top + size * 0.66, x - size * 0.10, top + size * 0.97),
               fill=BLACK, width=max(1, round(size * 0.10)))


def _icon_snow(d, cx: float, top: float, size: float) -> None:
    _cloud(d, cx, top + size * 0.32, size)
    r = size * 0.15
    for dx in (-0.22, 0.22):
        x, y = cx + dx * size, top + size * 0.82
        d.line((x - r, y, x + r, y), fill=BLACK, width=1)
        d.line((x, y - r, x, y + r), fill=BLACK, width=1)
        d.line((x - r * 0.7, y - r * 0.7, x + r * 0.7, y + r * 0.7), fill=BLACK, width=1)
        d.line((x - r * 0.7, y + r * 0.7, x + r * 0.7, y - r * 0.7), fill=BLACK, width=1)


def _hatch(d, box, spacing: int, fill: int, width: int = 2) -> None:
    """Diagonal hatching clipped to an axis-aligned box.

    Lines run along y - x = c, so for each c the visible segment is just the
    overlap of the box's x range with the x range that keeps y inside it.
    """
    x0, y0, x1, y1 = (round(v) for v in box)
    step = max(1, round(spacing * 1.414))  # perpendicular spacing, not along x
    for c in range(int(y0 - x1), int(y1 - x0) + 1, step):
        ax = max(x0, y0 - c)
        bx = min(x1, y1 - c)
        if bx > ax:
            d.line((ax, ax + c, bx, bx + c), fill=fill, width=width)


def _posterise(img: Image.Image, levels: int) -> Image.Image:
    step = 255 / (levels - 1)
    lut = [round(round(v / step) * step) for v in range(256)]
    return img.point(lut)


def _wrap(d, text: str, font, width: int, max_lines: int = 2) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if d.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    # Mark truncation rather than silently dropping the tail.
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        while lines[-1] and d.textlength(lines[-1] + "...", font=font) > width:
            lines[-1] = lines[-1].rsplit(" ", 1)[0] if " " in lines[-1] else lines[-1][:-1]
        lines[-1] += "..."
    return lines


def _temp(celsius: float, opts: RenderOptions) -> str:
    value = celsius * 9 / 5 + 32 if opts.imperial else celsius
    return f"{value:.0f}"


def _clock(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def _short_hour(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}{'a' if dt.hour < 12 else 'p'}"
