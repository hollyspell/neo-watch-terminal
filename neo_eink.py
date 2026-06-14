#!/usr/bin/env python3
"""
NEO WATCH / SENTRY — dual-view e-ink terminal firmware.

Renders two 800×480 1-bit images for a Waveshare 7.5" e-ink panel:
  View 1: NEO WATCH  — this week's close approaches (NASA NeoWs)
  View 2: SENTRY     — JPL Sentry risk-list objects with Palermo/Torino

Preview mode (no Pi hardware):
    python neo_eink.py --preview

Hardware mode (Raspberry Pi + Waveshare 7.5" v2):
    python neo_eink.py
    (button on GPIO 17 toggles views)

NASA API key: uses DEMO_KEY by default (30 req/hr).
Set NASA_API_KEY env var for a free personal key (1000 req/day).
"""

import json, math, os, sys, time, argparse
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

# ─── Configuration ────────────────────────────────────────────────────
W, H = 800, 480  # Waveshare 7.5" v2 resolution
NASA_KEY = os.environ.get("NASA_API_KEY", "DEMO_KEY")
NEOWS_URL = "https://api.nasa.gov/neo/rest/v1/feed"
SENTRY_URL = "https://ssd-api.jpl.nasa.gov/sentry.api"

FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"

# Preload fonts at sizes we need
def font(size, bold=False):
    return ImageFont.truetype(FONT_BOLD if bold else FONT_REG, size)

F32 = font(28, bold=True)
F20 = font(17)
F20B = font(17, bold=True)
F18 = font(15)
F16 = font(14)
F14 = font(12)
F24 = font(21, bold=True)
F36 = font(32, bold=True)

BLACK = 0
WHITE = 255
GRAY = 120  # dark enough to survive 1-bit dither as readable stipple

# ─── API fetching ─────────────────────────────────────────────────────

def fetch_neows():
    """Fetch this week's near-Earth object close approaches."""
    today = datetime.utcnow().date()
    end = today + timedelta(days=6)
    params = {
        "start_date": today.isoformat(),
        "end_date": end.isoformat(),
        "api_key": NASA_KEY,
    }
    try:
        r = requests.get(NEOWS_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"NeoWs fetch failed: {e}")
        return None, today, end

    objects = []
    for date_str, neos in data.get("near_earth_objects", {}).items():
        for neo in neos:
            ca = neo["close_approach_data"][0] if neo.get("close_approach_data") else None
            if not ca:
                continue
            dia = neo.get("estimated_diameter", {}).get("meters", {})
            avg_dia = (dia.get("estimated_diameter_min", 0) + dia.get("estimated_diameter_max", 0)) / 2
            miss_ld = float(ca["miss_distance"]["lunar"])
            objects.append({
                "name": neo.get("name", "?").strip("() "),
                "date": date_str,
                "miss_ld": miss_ld,
                "miss_km": float(ca["miss_distance"]["kilometers"]),
                "diameter_m": avg_dia,
                "velocity_kms": float(ca["relative_velocity"]["kilometers_per_second"]),
                "hazardous": neo.get("is_potentially_hazardous_asteroid", False),
            })
    # Keep only passes within 35 LD and sort by date
    objects = [o for o in objects if o["miss_ld"] <= 35]
    objects.sort(key=lambda o: (o["date"], o["miss_ld"]))
    return objects, today, end


def fetch_sentry():
    """Fetch JPL Sentry risk list, sorted by Palermo scale (descending)."""
    try:
        r = requests.get(SENTRY_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"Sentry fetch failed: {e}")
        return None

    items = []
    for obj in data.get("data", []):
        ps = float(obj.get("ps_cum", -99))
        ts = int(float(obj.get("ts_max", 0)))
        dia = obj.get("diameter", "?")
        ip = obj.get("ip", "?")
        name = obj.get("fullname", obj.get("des", "?")).strip()
        yr_range = obj.get("range", "?")
        items.append({
            "name": name,
            "palermo": ps,
            "torino": ts,
            "diameter_km": dia,
            "impact_prob": ip,
            "year_range": yr_range,
        })
    items.sort(key=lambda x: x["palermo"], reverse=True)
    return items


# ─── Drawing helpers ──────────────────────────────────────────────────

# ─── Ink illustration bitmaps for Earth & Moon ────────────────────────

# Pre-processed circular bitmaps from pen-and-ink illustrations
EARTH_BMP = os.path.join(os.path.dirname(__file__), "earth_bitmap.png")
MOON_BMP = os.path.join(os.path.dirname(__file__), "moon_bitmap.png")

_bitmap_cache = {}

def _load_bitmap(bmp_path, size):
    """Load a pre-rendered bitmap and resize to target."""
    key = (bmp_path, size)
    if key in _bitmap_cache:
        return _bitmap_cache[key]
    try:
        img = Image.open(bmp_path).convert("L")
        img = img.resize((size, size), Image.LANCZOS)
    except FileNotFoundError:
        img = Image.new("L", (size, size), WHITE)
        d = ImageDraw.Draw(img)
        d.ellipse([2, 2, size-3, size-3], fill=BLACK)
        print(f"Bitmap not found: {bmp_path} — using fallback")
    _bitmap_cache[key] = img
    return img


def paste_earth(target_img, cx, cy, diameter):
    """Paste the ink-illustration Earth onto a grayscale image."""
    bmp = _load_bitmap(EARTH_BMP, diameter)
    x = cx - diameter // 2
    y = cy - diameter // 2
    target_img.paste(bmp, (x, y))


def paste_moon(target_img, cx, cy, diameter):
    """Paste the ink-illustration Moon onto a grayscale image."""
    bmp = _load_bitmap(MOON_BMP, diameter)
    x = cx - diameter // 2
    y = cy - diameter // 2
    target_img.paste(bmp, (x, y))


def ld_to_y(ld, y_top, y_bottom):
    """Map lunar distance (log scale) to y pixel. ld in [0.3, 35] → [y_bottom, y_top]."""
    if ld <= 0:
        ld = 0.3
    log_min, log_max = math.log10(0.3), math.log10(35)
    frac = (math.log10(ld) - log_min) / (log_max - log_min)
    return int(y_bottom - frac * (y_bottom - y_top))


def dia_to_radius(dia_m):
    """Object diameter (meters) → dot radius in pixels (sqrt scale, clamped)."""
    r = max(3, min(22, int(2.5 * math.sqrt(dia_m / 10))))
    return r


# ─── View 1: NEO WATCH ───────────────────────────────────────────────

def render_neo_watch(objects, date_start, date_end):
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # ── Header ──
    draw.text((20, 12), "NEO WATCH", font=F32, fill=BLACK)
    date_str = f"{date_start.strftime('%a %d %b')} – {date_end.strftime('%a %d %b %Y')}"
    bbox = draw.textbbox((0, 0), date_str, font=F18)
    draw.text((W - 20 - (bbox[2] - bbox[0]), 18), date_str, font=F18, fill=BLACK)
    draw.line([(20, 50), (W-20, 50)], fill=BLACK, width=3)

    # ── Subtitle ──
    count = len(objects) if objects else 0
    sub = f"Miss distance (log LD) · dot area = est. diameter · {count} objects"
    draw.text((20, 58), sub, font=F14, fill=GRAY)

    # ── Chart area ──
    chart_left = 60
    chart_right = W - 20
    chart_top = 82
    chart_bot = 340
    earth_x = chart_left + 30

    # Grid lines
    for ld_val, label in [(30, "30"), (10, "10"), (3, "3")]:
        y = ld_to_y(ld_val, chart_top, chart_bot)
        draw.line([(chart_left, y), (chart_right, y)], fill=220, width=1)
        bbox = draw.textbbox((0, 0), label, font=F14)
        draw.text((chart_left - 6 - (bbox[2]-bbox[0]), y - 7), label, font=F14, fill=GRAY)

    # Moon line at 1 LD
    moon_y = ld_to_y(1.0, chart_top, chart_bot)
    # Dashed line
    dash_len = 8
    for x in range(chart_left, chart_right, dash_len * 2):
        draw.line([(x, moon_y), (min(x + dash_len, chart_right), moon_y)], fill=BLACK, width=2)
    draw.text((chart_left - 6 - draw.textbbox((0,0), "1", font=F14)[2], moon_y - 7),
              "1", font=F14, fill=GRAY)

    # Earth baseline
    draw.line([(chart_left, chart_bot + 4), (chart_right, chart_bot + 4)], fill=BLACK, width=3)

    # Draw Earth
    paste_earth(img, earth_x, chart_bot + 4, 56)

    # Draw Moon
    moon_x = chart_right - 20
    paste_moon(img, moon_x, moon_y, 32)

    # ── Day columns ──
    if objects and date_start:
        days = [(date_start + timedelta(days=i)) for i in range(7)]
        day_labels = [d.strftime("%a") for d in days]
        day_strs = [d.isoformat() for d in days]

        col_width = (chart_right - chart_left - 80) / 7  # leave room for earth & moon
        col_starts = [chart_left + 70 + int(i * col_width + col_width / 2) for i in range(7)]

        # Day labels below baseline
        for i, (lbl, cx) in enumerate(zip(day_labels, col_starts)):
            bbox = draw.textbbox((0, 0), lbl, font=F16)
            draw.text((cx - (bbox[2]-bbox[0])//2, chart_bot + 14), lbl, font=F16, fill=BLACK)

        # Plot objects — group by day, jitter within column if needed
        day_objects = {ds: [] for ds in day_strs}
        for obj in objects:
            if obj["date"] in day_objects:
                day_objects[obj["date"]].append(obj)

        for i, ds in enumerate(day_strs):
            objs = day_objects.get(ds, [])
            cx = col_starts[i]
            for j, obj in enumerate(objs):
                y = ld_to_y(obj["miss_ld"], chart_top, chart_bot)
                r = dia_to_radius(obj["diameter_m"])
                # Jitter horizontally if multiple on same day
                jitter = (j - len(objs)/2) * (r + 4) if len(objs) > 1 else 0
                ox = int(cx + jitter)
                oy = y

                fill = GRAY if obj.get("hazardous") else BLACK
                draw.ellipse([ox-r, oy-r, ox+r, oy+r], fill=fill)

                # Label — short name
                name = obj["name"]
                # Shorten long names
                if len(name) > 10:
                    parts = name.split()
                    name = parts[-1] if len(parts) > 1 else name[:8]
                bbox = draw.textbbox((0, 0), name, font=F14)
                tw = bbox[2] - bbox[0]
                label_y = oy - r - 16
                if label_y < chart_top:
                    label_y = oy + r + 3
                draw.text((ox - tw//2, label_y), name, font=F14, fill=BLACK)

                # Size label below
                if obj["diameter_m"] >= 10:
                    size_str = f"{int(obj['diameter_m'])}m"
                    bbox2 = draw.textbbox((0, 0), size_str, font=F14)
                    sw = bbox2[2] - bbox2[0]
                    sz_y = oy + r + 3
                    if label_y == oy + r + 3:
                        sz_y = label_y + 14
                    if sz_y < chart_bot - 2:
                        draw.text((ox - sw//2, sz_y), size_str, font=F14, fill=GRAY)
    else:
        draw.text((W//2 - 80, 200), "No data — check API", font=F20, fill=BLACK)

    # ── Stat boxes ──
    box_y = chart_bot + 40
    box_h = 52
    box_w = (W - 60) // 3

    stats = _compute_stats(objects)
    for i, (label, value, sub) in enumerate(stats):
        bx = 20 + i * (box_w + 10)
        draw.rectangle([bx, box_y, bx + box_w, box_y + box_h], outline=BLACK, width=2)
        draw.text((bx + 8, box_y + 4), label, font=F14, fill=GRAY)
        draw.text((bx + 8, box_y + 22), value, font=F20B, fill=BLACK)
        # Sub text (day) to right of value
        vbbox = draw.textbbox((0, 0), value, font=F20B)
        draw.text((bx + 14 + (vbbox[2]-vbbox[0]), box_y + 24), sub, font=F14, fill=GRAY)

    # ── Footer ──
    draw.text((20, H - 22), "NASA NeoWs · refreshed " + datetime.utcnow().strftime("%H:%M UTC"),
              font=F14, fill=GRAY)

    return img


def _compute_stats(objects):
    if not objects:
        return [("Closest", "—", ""), ("Largest", "—", ""), ("Fastest", "—", "")]
    closest = min(objects, key=lambda o: o["miss_ld"])
    largest = max(objects, key=lambda o: o["diameter_m"])
    fastest = max(objects, key=lambda o: o["velocity_kms"])
    c_day = datetime.fromisoformat(closest["date"]).strftime("%a")
    l_day = datetime.fromisoformat(largest["date"]).strftime("%a")
    f_day = datetime.fromisoformat(fastest["date"]).strftime("%a")
    return [
        ("Closest", f"{closest['miss_ld']:.1f} LD", c_day),
        ("Largest", f"{int(largest['diameter_m'])}m", l_day),
        ("Fastest", f"{fastest['velocity_kms']:.1f} km/s", f_day),
    ]


# ─── View 2: SENTRY ──────────────────────────────────────────────────

def render_sentry(items):
    img = Image.new("L", (W, H), WHITE)
    draw = ImageDraw.Draw(img)

    # ── Header ──
    max_torino = 0
    if items:
        max_torino = max(it["torino"] for it in items)
    draw.text((20, 12), "SENTRY", font=F32, fill=BLACK)
    torino_str = f"Jun {datetime.utcnow().year} · all Torino {max_torino}"
    bbox = draw.textbbox((0, 0), torino_str, font=F18)
    draw.text((W - 20 - (bbox[2]-bbox[0]), 18), torino_str, font=F18, fill=BLACK)
    draw.line([(20, 50), (W-20, 50)], fill=BLACK, width=3)

    # ── Left panel: ranked objects ──
    mid_x = 450  # divider x — give right panel more room
    draw.text((20, 58), "Highest-rated objects", font=F20B, fill=BLACK)
    draw.text((20, 78), "Ranked by Palermo scale", font=F14, fill=GRAY)

    # Zero line for bars — placed right of object names
    zero_x = mid_x - 60
    bar_top = 100
    bar_spacing = 54

    # Vertical zero reference line
    n_show = min(5, len(items) if items else 0)
    if n_show > 0:
        draw.line([(zero_x, bar_top - 4), (zero_x, bar_top + n_show * bar_spacing - 16)],
                  fill=BLACK, width=2)
        draw.text((zero_x + 4, bar_top - 16), "0", font=F14, fill=GRAY)

    top_items = items[:n_show] if items else []
    for i, it in enumerate(top_items):
        y = bar_top + i * bar_spacing
        # Name
        draw.text((20, y), it["name"], font=F20B, fill=BLACK)
        # Details line
        dia_str = it["diameter_km"]
        if isinstance(dia_str, str):
            detail = f"{dia_str} km"
        else:
            detail = f"{float(dia_str):.2f} km"
        ip_str = it["impact_prob"]
        yr_str = it["year_range"]
        detail += f" · {ip_str} · {yr_str}"
        draw.text((20, y + 18), detail, font=F14, fill=GRAY)

        # Palermo bar — grows LEFT from zero_x
        ps = it["palermo"]
        bar_len = max(4, int((ps + 8) / 8 * 180))
        bar_left = zero_x - bar_len
        fill_c = BLACK if ps > -2 else GRAY
        draw.rectangle([bar_left, y + 4, zero_x, y + 14], fill=fill_c)
        # Value label to the left of the bar
        ps_str = f"{ps:.2f}"
        bbox = draw.textbbox((0, 0), ps_str, font=F14)
        draw.text((bar_left - (bbox[2]-bbox[0]) - 5, y + 1), ps_str, font=F14, fill=BLACK)

    if not items:
        draw.text((20, 110), "No data — check API", font=F20, fill=BLACK)

    # ── Vertical divider ──
    draw.line([(mid_x, 56), (mid_x, 368)], fill=180, width=1)

    # ── Right panel: scale explanations ──
    rx = mid_x + 18

    draw.text((rx, 58), "Palermo scale", font=F20B, fill=BLACK)
    palermo_lines = [
        "Compares impact probability",
        "to average background risk of",
        "all objects that size over the",
        "same time span.",
        "",
        " 0  same as background noise",
        "−2  1% of background",
        "−8  negligible",
        "+1  10× background (never seen)",
        "",
        "All values below zero means",
        "nothing on this list exceeds",
        "the steady random chance from",
        "undiscovered objects.",
    ]
    for j, line in enumerate(palermo_lines):
        y = 82 + j * 17
        f = F14 if not line.startswith((" ", "−", "+")) else F14
        c = BLACK if line.startswith((" ", "−", "+")) else GRAY
        if line == "":
            continue
        draw.text((rx, y), line, font=F14, fill=c)

    # ── Torino section ──
    draw.line([(20, 376), (W-20, 376)], fill=BLACK, width=2)

    draw.text((20, 386), "Torino scale", font=F20B, fill=BLACK)
    torino_lines = [
        "Public hazard rating, 0–10. 0 = no hazard or too small to",
        "survive atmosphere. No object has ever exceeded Torino 1.",
    ]
    for j, line in enumerate(torino_lines):
        draw.text((20, 406 + j * 16), line, font=F14, fill=GRAY)

    # Big Torino number
    draw.text((rx, 384), str(max_torino), font=F36, fill=BLACK)
    t_lines = [
        "Current maximum Torino rating",
        "across all tracked objects.",
        "The sky is quiet.",
    ]
    for j, line in enumerate(t_lines):
        draw.text((rx + 40, 388 + j * 16), line, font=F14, fill=GRAY)

    # Footer
    draw.text((20, H - 20),
              "JPL Sentry · checked " + datetime.utcnow().strftime("%d %b %Y"),
              font=F14, fill=GRAY)

    return img


# ─── 1-bit conversion (Floyd-Steinberg dither) ───────────────────────

def to_1bit(img):
    """Convert grayscale image to 1-bit with Floyd-Steinberg dithering."""
    return img.convert("1")


# ─── Hardware driver (Waveshare 7.5" v2) ─────────────────────────────

def push_to_display(img_1bit):
    """Push a 1-bit PIL image to the Waveshare 7.5" v2 e-ink display."""
    try:
        from waveshare_epd import epd7in5_V2
        epd = epd7in5_V2.EPD()
        epd.init()
        epd.Clear()
        epd.display(epd.getbuffer(img_1bit))
        epd.sleep()
    except ImportError:
        print("waveshare_epd not found — run on a Pi with the driver installed.")
    except Exception as e:
        print(f"Display error: {e}")


def run_hardware_loop():
    """Main loop for Pi: fetch data, render, push to display, handle button."""
    try:
        import RPi.GPIO as GPIO
    except ImportError:
        print("RPi.GPIO not available. Use --preview for desktop testing.")
        return

    BUTTON_PIN = 17
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    print("Fetching data...")
    neo_objs, d_start, d_end = fetch_neows()
    sentry_items = fetch_sentry()

    img_neo = to_1bit(render_neo_watch(neo_objs, d_start, d_end))
    img_sentry = to_1bit(render_sentry(sentry_items))

    views = [img_neo, img_sentry]
    current = 0
    last_fetch = time.time()

    print("Pushing NEO WATCH to display...")
    push_to_display(views[current])

    try:
        while True:
            # Button press → toggle view
            if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                time.sleep(0.05)  # debounce
                if GPIO.input(BUTTON_PIN) == GPIO.LOW:
                    current = 1 - current
                    name = "NEO WATCH" if current == 0 else "SENTRY"
                    print(f"Switching to {name}")
                    push_to_display(views[current])
                    while GPIO.input(BUTTON_PIN) == GPIO.LOW:
                        time.sleep(0.05)

            # Refresh data every hour
            if time.time() - last_fetch > 3600:
                print("Hourly refresh...")
                neo_objs, d_start, d_end = fetch_neows()
                sentry_items = fetch_sentry()
                img_neo = to_1bit(render_neo_watch(neo_objs, d_start, d_end))
                img_sentry = to_1bit(render_sentry(sentry_items))
                views = [img_neo, img_sentry]
                push_to_display(views[current])
                last_fetch = time.time()

            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        GPIO.cleanup()


# ─── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NEO WATCH / SENTRY e-ink terminal")
    parser.add_argument("--preview", action="store_true",
                        help="Render preview PNGs instead of pushing to hardware")
    parser.add_argument("--mock", action="store_true",
                        help="Use mock data (skip API calls)")
    args = parser.parse_args()

    if args.mock:
        neo_objs = _mock_neo()
        sentry_items = _mock_sentry()
        d_start = datetime.utcnow().date()
        d_end = d_start + timedelta(days=6)
    else:
        print("Fetching NeoWs data...")
        neo_objs, d_start, d_end = fetch_neows()
        print(f"  {len(neo_objs) if neo_objs else 0} objects within 35 LD")
        print("Fetching Sentry data...")
        sentry_items = fetch_sentry()
        print(f"  {len(sentry_items) if sentry_items else 0} risk-list objects")

    # Render grayscale
    img_neo = render_neo_watch(neo_objs, d_start, d_end)
    img_sentry = render_sentry(sentry_items)

    if args.preview:
        # Save both grayscale (easier to review) and 1-bit (actual display)
        out = Path(".")
        img_neo.save(out / "neo_watch_gray.png")
        img_sentry.save(out / "sentry_gray.png")
        to_1bit(img_neo).save(out / "neo_watch_1bit.png")
        to_1bit(img_sentry).save(out / "sentry_1bit.png")
        print("Saved: neo_watch_gray.png, neo_watch_1bit.png, sentry_gray.png, sentry_1bit.png")

        # Also save a combined "both screens" preview with the green bezel
        combo = _render_combo(img_neo, img_sentry)
        combo.save(out / "preview_combo.png")
        print("Saved: preview_combo.png")
    else:
        run_hardware_loop()


def _render_combo(img1, img2):
    """Side-by-side preview with green terminal bezel."""
    pad = 30
    bezel = 20
    single_w = W + bezel * 2
    single_h = H + bezel * 2
    total_w = single_w * 2 + pad * 3
    total_h = single_h + pad * 2

    combo = Image.new("RGB", (total_w, total_h), (42, 42, 40))

    for i, img in enumerate([img1, img2]):
        bx = pad + i * (single_w + pad)
        by = pad
        # Green bezel
        for y in range(single_h):
            for x in range(single_w):
                combo.putpixel((bx + x, by + y), (157, 171, 147))
        # Inner dark border
        for y in range(bezel - 6, single_h - bezel + 6):
            for x in range(bezel - 6, single_w - bezel + 6):
                combo.putpixel((bx + x, by + y), (42, 42, 40))
        # Screen content
        screen = img.convert("RGB")
        combo.paste(screen, (bx + bezel, by + bezel))

    return combo


def _mock_neo():
    today = datetime.utcnow().date()
    return [
        {"name": "2026 LK3", "date": (today).isoformat(), "miss_ld": 0.8,
         "miss_km": 307000, "diameter_m": 18, "velocity_kms": 8.2, "hazardous": False},
        {"name": "2026 KQ", "date": (today + timedelta(1)).isoformat(), "miss_ld": 3.1,
         "miss_km": 1190000, "diameter_m": 45, "velocity_kms": 21.4, "hazardous": False},
        {"name": "2008 DG5", "date": (today + timedelta(2)).isoformat(), "miss_ld": 26.0,
         "miss_km": 9990000, "diameter_m": 600, "velocity_kms": 14.1, "hazardous": True},
        {"name": "2010 NY65", "date": (today + timedelta(3)).isoformat(), "miss_ld": 8.6,
         "miss_km": 3300000, "diameter_m": 230, "velocity_kms": 12.3, "hazardous": True},
        {"name": "1994 XD", "date": (today + timedelta(5)).isoformat(), "miss_ld": 12.4,
         "miss_km": 4760000, "diameter_m": 400, "velocity_kms": 16.8, "hazardous": True},
        {"name": "2026 MB1", "date": (today + timedelta(6)).isoformat(), "miss_ld": 5.2,
         "miss_km": 1997000, "diameter_m": 30, "velocity_kms": 9.5, "hazardous": False},
    ]


def _mock_sentry():
    return [
        {"name": "101955 Bennu", "palermo": -1.41, "torino": 0,
         "diameter_km": "0.49", "impact_prob": "3.7e-04", "year_range": "2178-2290"},
        {"name": "29075 (1950 DA)", "palermo": -0.97, "torino": 0,
         "diameter_km": "1.30", "impact_prob": "2.9e-05", "year_range": "2880"},
        {"name": "1979 XB", "palermo": -2.71, "torino": 0,
         "diameter_km": "0.66", "impact_prob": "5.6e-07", "year_range": "2056-2113"},
        {"name": "2000 SG344", "palermo": -2.77, "torino": 0,
         "diameter_km": "0.037", "impact_prob": "9.1e-04", "year_range": "2069-2122"},
        {"name": "2010 RF12", "palermo": -3.32, "torino": 0,
         "diameter_km": "0.007", "impact_prob": "9.5e-02", "year_range": "2095"},
    ]


if __name__ == "__main__":
    main()
