#!/usr/bin/env python3
"""
================================================================================
Rivian R2 Living Art Frame - Architectural Blueprint E-Ink Mockup Generator
Target Display: Inkplate 10 (1200x825 e-paper display)
Library: Pillow (PIL)
Typography: Neucha (SIL OFL Architectural Hand-Printed)
================================================================================
"""

from datetime import datetime
import json
import os
import sys
import requests

import vehicles
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from fetch_telemetry import (
    KM_TO_MILES,
    METERS_TO_MILES,
    authenticate_with_refresh_token,
    fetch_vehicle_telemetry,
    get_r2_vehicle_and_born_on_date,
    load_config,
)

# ==============================================================================
# 1. MOCK TELEMETRY DATA
# ==============================================================================
TELEMETRY = {
    # Vehicle Identity & Blueprint Metadata
    "vehicleName": "RIVIAN R2",
    "blueprintSubtitle": "CHASSIS & TELEMETRY SCHEMATIC",
    "vehicleStatus": "ONLINE",
    "lastUpdated": "TODAY  •  10:42 AM",
    "drawingNumber": "DWG NO. R2-EPD-01",
    "revision": "REV 1.0",
    "actualGeneralAssemblyDate": "2026-01-01",

    # High Voltage Battery & Range
    "batteryLevel": 78,                     # Percentage: 0 to 100%
    "batteryLimit": 85,                     # Target charge limit %
    "batteryCapacity": 90.9,                # Usable HV Pack capacity in kWh
    "distanceToEmpty": 264,                 # Estimated miles remaining
    "chargerState": "DISCONNECTED",         # Options: "DISCONNECTED", "CHARGING", "PLUGGED_IN"
    "timeToEndOfCharge": 0,                 # Minutes remaining when actively charging
    "scheduleStartTime": 60,                # Minutes past midnight (60 = 1:00 AM)

    # Battery Temperature & Auxiliary 12V Status
    "batteryTemperature": 21.6,             # HV Pack avg temperature in Celsius, converted to °F
    "batteryTempMin": 19.4,                 # HV Pack min cell temperature in Celsius
    "batteryTempMax": 21.8,                 # HV Pack max cell temperature in Celsius
    "battery12VState": "NORMAL",            # Telemetry health state for 12V auxiliary system

    # Odometer
    "vehicleMileage": 14820,                # Total vehicle miles

    # Cabin Climate
    "cabinClimateInteriorTemperature": 21.0, # Stored in Celsius, automatically converted to °F
    "cabinClimateExteriorTemperature": 19.8, # Stored in Celsius, automatically converted to °F

    # Tire Pressures (in PSI)
    "tirePressureFrontLeft": 38.0,
    "tirePressureFrontRight": 38.0,
    "tirePressureRearLeft": 39.0,
    "tirePressureRearRight": 39.0,
}

# ==============================================================================
# 2. DISPLAY & LAYOUT CONFIGURATION
# ==============================================================================
CONFIG = {
    # Canvas dimensions for Inkplate 10
    "canvas_width": 1200,
    "canvas_height": 820,
    # When True (set by build_sd_assets.py), applies aggressive E-Ink waveform
    # compensation. When False (default for r2_dashboard_mockup.png), preserves
    # smooth 256-level grayscale anti-aliasing for LED monitor viewing.
    "output_file": "local/previews/r2_dashboard_mockup.png",
    "eink_panel_mode": False,
    "wireframe_file": "assets/wireframes/r2_wireframe.png",

    # Architectural Grayscale Palette (0 = deep black, 255 = blueprint paper white)
    # All structural lines, borders, and text use pure black (0) for e-paper legibility.
    "colors": {
        "black": 0,            # Primary text, active battery fill, sharp drafting lines
        "dark_gray": 0,        # Secondary technical labels, subtitles (pure black for e-paper)
        "mid_gray": 0,         # Outlines, ticks, dimension lines, leader lines (pure black)
        "light_gray": 0,       # Inner card dividers & accent borders (pure black)
        "white": 255,          # Canvas and card background fill
    },

    # ==========================================================================
    # >>> TYPOGRAPHY & MINIMUM FONT SIZE CONFIGURATION <<<
    # Optimized for Inkplate 10 e-paper legibility (~150 DPI e-reader screen).
    # Minimum font size is set to 14px (slightly larger than the previous
    # 12px status line), scaling up card subtitles, card headers, and ground text.
    # ==========================================================================
    "fonts": {
        "min_size": 18,
        "title": 34,             # Scaled up from 27
        "subtitle": 19,          # Scaled up from 14
        "meta_drawing": 20,      # Scaled up from 15
        "meta_status": 19,       # Scaled up from 14
        "card_header": 18,       # Scaled up from 14
        "card_value": 26,        # Scaled up from 20
        "card_sub": 18,          # Scaled up from 14
        "card_inline": 17,       # Inline MIN/MAX temp on Card 4
        "tire_label": 18,        # Scaled up from 14
        "tire_value": 22,        # Scaled up from 17
        "ground_datum": 19,      # Scaled up from 14
        "ground_footer": 19,     # Scaled up from 14
    },

    # ==========================================================================
    # >>> TERMINAL DOT TARGET COORDINATES <<<
    # Positioned to match the vehicle wireframe at (X=35, Y=310).
    # ==========================================================================
    "terminal_dots": {
        # Information Card Leader Line Target Dots (on vehicle body)
        "odometer":   (415, 450),  # Card 02 -> Behind dash / instrument cluster
        "climate":    (575, 385),  # Card 03 -> Middle of driver's window
        "battery12v": (800, 575),  # Card 04 -> Under rear seat cushion
        "charger":    (1085, 510), # Card 05 -> Charger door on rear quarter panel

        # Underbody Tire Pressure Leader Line Target Dots (on wheels)
        "tire_fl":    (300, 661),  # Front L -> Front wheel rim
        "tire_fr":    (305, 701),  # Front R -> Lower front tire
        "tire_rl":    (875, 701),  # Rear L  -> Lower rear tire
        "tire_rr":    (830, 661),  # Rear R  -> Rear wheel rim
    },

    "layout": {
        # ----------------------------------------------------------------------
        # Architectural Border & Corner Crosshairs
        # ----------------------------------------------------------------------
        "blueprint_frame": {
            "enabled": True,
            "margin_x": 20,
            "margin_y": 18,
            "border_width": 2,
            "corner_cross_size": 10,
        },

        # ----------------------------------------------------------------------
        # Top Header Bar: Rivian Compass Logo, Architectural Title & Title Block
        # Vertically centered between upper border (Y=18) and divider (Y=108), midpoint Y=63
        # ----------------------------------------------------------------------
        "header": {
            "logo_x": 36,
            "logo_y": 26,
            "logo_size": 74,
            "title_x": 124,
            "title_y": 37,
            "subtitle_y": 73,
            "meta_right_x": 1164,   # Right edge for technical status metadata
            "meta_y1": 41,
            "meta_y2": 71,
            "divider_y": 108,       # Horizontal separator line
        },

        # ----------------------------------------------------------------------
        # Top Margin Metric Cards: 5 cards across the screen
        # 1. 01 // BATTERY LEVEL (downsized bargraph + est. range)
        # 2. 02 // ODOMETER      (leader line to dash)
        # 3. 03 // CLIMATE       (leader line to driver's window)
        # 4. 04 // BATTERY STATE (leader line under rear seat cushion)
        # 5. 05 // CHARGER       (leader line to charger door)
        # ----------------------------------------------------------------------
        "metrics_grid": {
            "start_x": 36,
            "y": 120,
            "card_width": 216,
            "card_height": 124,
            "card_gap": 12,
            "corner_radius": 0,     # Sharp technical rectangle corners
            "border_width": 2,
            "show_corner_ticks": True, # Architectural drafting cross-ticks at corners

            # Leader line styling from cards to car
            "draw_card_leaders": True,
            "card_leader_width": 2,
            "terminal_dot_radius": 4,
            "shoulder_length": 14,
        },

        # ----------------------------------------------------------------------
        # Vehicle Wireframe Base Image (1130x430)
        # Centered vertically at Y=310 (ends at Y=740) to balance top/bottom margins.
        # ----------------------------------------------------------------------
        "wireframe": {
            "x": 35,                # Centered horizontally: (1200 - 1130) // 2 = 35
            "y": 310,               # Vertically balanced inside outer frame
            "ground_y": 740,        # Ground line aligning with tire contact patches
            "high_contrast": True,  # Converts gray alpha line art to pure black for e-ink
        },

        # ----------------------------------------------------------------------
        # Tire Pressure Callouts & Technical Leader Lines
        # Placed under the car, in the white space between the tires (X=360..750)
        # and above the ground line (Y=660..718).
        # ----------------------------------------------------------------------
        "tire_pressures": {
            "badge_width": 88,
            "badge_height": 58,
            "corner_radius": 0,     # Sharp drafting box
            "draw_leader_lines": True,
            "leader_line_width": 2,
            "terminal_dot_radius": 4,

            # Front pair badges (positioned towards front wheel on left side)
            "front_left": {
                "x": 360,
                "y": 660,
                "label": "Front L",
                "leader_start": (360, 688),
                "leader_mid": (330, 688),
            },
            "front_right": {
                "x": 456,
                "y": 660,
                "label": "Front R",
                "leader_start": (500, 718),
                "leader_mid1": (500, 729),
                "leader_mid2": (340, 729),
            },

            # Rear pair badges (positioned towards rear wheel on right side)
            "rear_left": {
                "x": 566,
                "y": 660,
                "label": "Rear L",
                "leader_start": (610, 718),
                "leader_mid1": (610, 729),
                "leader_mid2": (810, 729),
            },
            "rear_right": {
                "x": 662,
                "y": 660,
                "label": "Rear R",
                "leader_start": (750, 688),
                "leader_mid": (785, 688),
            },
        },
    }
}

# ==============================================================================
# 2b. ACTIVE VEHICLE
# The values above describe the R2. Other vehicles are profiles in
# tools/vehicles.py, selected with `--vehicle r1t`, FRAME_VEHICLE=r1t, or
# "vehicle_model" in config.json.
# ==============================================================================
VEHICLE = vehicles.selected_vehicle()
_profile = vehicles.profile(VEHICLE)
TELEMETRY["vehicleName"] = _profile["name"]
CONFIG["charger_card_first"] = bool(_profile.get("charger_card_first", False))
CONFIG["output_file"] = f"local/previews/{VEHICLE}_dashboard_mockup.png"
for _key in ("wireframe_file", "terminal_dots"):
    if _key in _profile:
        CONFIG[_key] = _profile[_key]

# ==============================================================================
# 3. ARCHITECTURAL FONT LOADER
# ==============================================================================
def get_architect_font(size: int, bold: bool = False, _raw_size: bool = False):
    """
    Loads 'Neucha' (SIL OFL) as the primary architectural hand-drafting font.
    Falls back gracefully to other bundled or system fonts if needed.
    """
    local_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        # Neucha (Open-licensed SIL OFL architectural hand-printed font)
        os.path.join(local_dir, "assets", "fonts", "Neucha.ttf"),
        # Optional local commercial/freeware drafting fonts if supplied by user
        os.path.join(local_dir, "local", "fonts", "TektonPro-Bold.otf"),
        os.path.join(local_dir, "local", "fonts", "Flux Architect Regular.ttf"),
        os.path.join(local_dir, "local", "fonts", "ArchitectsDaughter-Regular.ttf"),
        # System candidates
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            try:
                eff_size = size
                if not _raw_size and os.path.basename(path).lower().startswith("neucha"):
                    eff_size = int(round(size * 1.10))
                idx = 1 if (bold and path.endswith(".ttc")) else 0
                return ImageFont.truetype(path, eff_size, index=idx)
            except Exception:
                continue

    return ImageFont.load_default()

# ==============================================================================
# 4. DRAWING ROUTINES
# ==============================================================================

def draw_crisp_text(draw: ImageDraw.ImageDraw, xy: tuple, text: str, font, fill: int = 0) -> None:
    """
    Renders 4x-supersampled anti-aliased text.
    - In desktop mockup mode (eink_panel_mode=False), preserves smooth 256-level LANCZOS
      anti-aliasing with a gentle continuous gamma curve.
    - In E-Ink panel mode (eink_panel_mode=True), applies subpixel weight and contrast
      boost to resist GS4 waveform fringe bleaching on the physical Inkplate 10.
    """
    scale = 4
    sz = getattr(font, "size", 18)
    f_hi = get_architect_font(int(sz * scale), bold=True, _raw_size=True)
    bbox = f_hi.getbbox(text)
    if not bbox:
        return
    x, y = int(xy[0]), int(xy[1])
    x0, y0, x1, y1 = bbox
    pad = 8
    w_hi = ((x1 - x0 + pad * 2 + scale - 1) // scale) * scale
    h_hi = ((y1 - y0 + pad * 2 + scale - 1) // scale) * scale
    hi = Image.new("L", (w_hi, h_hi), 0)
    hd = ImageDraw.Draw(hi)

    if CONFIG.get("eink_panel_mode", False):
        if sz <= 22:
            for dx in (0, 1, 2):
                for dy in (0, 1):
                    hd.text((-x0 + pad + dx, -y0 + pad + dy), text, fill=255, font=f_hi)
            core_thresh = 138
        else:
            for dx in (-1, 0, 1):
                for dy in (0, 1):
                    hd.text((-x0 + pad + dx, -y0 + pad + dy), text, fill=255, font=f_hi)
            core_thresh = 128
        lo = hi.resize((w_hi // scale, h_hi // scale), resample=Image.Resampling.LANCZOS)

        def _eink_curve(a: int) -> int:
            if a >= core_thresh:
                return 255
            if a >= 38:
                return int(95 + (a - 38) * 160 / (core_thresh - 38))
            return 0

        alpha_mask = Image.eval(lo, _eink_curve)
    else:
        hd.text((-x0 + pad, -y0 + pad), text, fill=255, font=f_hi)
        lo = hi.resize((w_hi // scale, h_hi // scale), resample=Image.Resampling.LANCZOS)
        alpha_mask = Image.eval(lo, lambda a: int(((a / 255.0) ** 0.82) * 255))

    draw._image.paste(fill, (x + (x0 - pad) // scale, y + (y0 - pad) // scale), alpha_mask)


def draw_reversed_badge(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    text: str,
    font,
    pad_x: int = 5,
    pad_y: int = 3,
    box_w: int = None,
) -> int:
    """Draws a solid black (0) warning rectangle and renders white (255) text inside it."""
    x, y = int(xy[0]), int(xy[1])
    bbox = font.getbbox(text)
    if not bbox:
        return 0
    x0, y0, x1, y1 = bbox
    tw = x1 - x0
    rect_w = box_w if box_w is not None else (tw + pad_x * 2)
    rx0 = x - pad_x
    ry0 = y + y0 - pad_y
    rx1 = rx0 + rect_w
    ry1 = y + y1 + pad_y
    draw.rectangle([rx0, ry0, rx1, ry1], fill=0, outline=0)
    tx = x + ((rect_w - pad_x * 2 - tw) // 2 if box_w is not None else 0)
    draw_crisp_text(draw, (tx, y), text, font, fill=255)
    return rect_w


RIVIAN_COMPASS_SVG_PATH = (
    "m22.102 24.256c1.0383-0.9483 2.8675-2.7223 3.5481-3.5174 0.3106-0.3515 0.2902-0.7909 0.2902-0.7909"
    "s0.0204-0.4395-0.2902-0.791c-0.6806-0.795-2.5098-2.5691-3.5481-3.5174-0.4251-0.3965-0.5273-0.6458-0.5273-1.1323"
    "v-5.3772c0-0.25548 0.2432-0.36789 0.4497-0.18803 2.3156 2.144 7.4742 7.2556 9.2032 9.236 0.8257 0.8768 0.7501 1.77 0.7501 1.77"
    "s0.0756 0.8911-0.7501 1.7699c-1.7311 1.9805-6.8897 7.092-9.2032 9.236-0.2065 0.1799-0.4497 0.0674-0.4497-0.188"
    "v-5.3773c0.0021-0.4885 0.1022-0.7358 0.5273-1.1323zm-3.0453-0.0041c-1.0382-0.9483-2.8674-2.7224-3.548-3.5174"
    "-0.3107-0.3515-0.2903-0.791-0.2903-0.791s-0.0204-0.4394 0.2903-0.7909c0.6806-0.7951 2.5098-2.5691 3.548-3.5174"
    " 0.4251-0.3965 0.5273-0.6459 0.5273-1.1323v-5.3936c0-0.21664-0.2534-0.35154-0.4496-0.16964-2.3156 2.144-7.4742 7.2556-9.2033 9.236"
    "-0.82774 0.8768-0.75008 1.77-0.75008 1.77s-0.07562 0.8911 0.75008 1.7699c1.7311 1.9805 6.8897 7.0921 9.2033 9.236"
    " 0.2064 0.1799 0.4496 0.0675 0.4496-0.188v-5.3773c0-0.4905-0.1022-0.7398-0.5273-1.1343zm-18.977-2.749"
    "c2.3279 2.4015 14.922 15.392 17.282 17.266 0.8237 0.6602 1.8844 1.1446 3.2149 1.1446 1.3326 0 2.3933-0.4844 3.215-1.1446"
    " 2.3606-1.8741 14.954-14.865 17.282-17.266 0.19-0.2187 0.0183-0.562-0.2678-0.562-1.1629 0-3.8587-0.0041-5.4835-0.0041"
    "-0.5723 0-0.7419 0.1001-1.1017 0.4864-0.3597 0.3863-2.15 2.3075-2.15 2.3075-2.2769 2.4546-7.8299 7.8748-9.8594 9.5834"
    "-0.7623 0.6418-1.6248 0.6213-1.6371 0.6213s-0.8748 0.0205-1.6371-0.6213c-2.0295-1.7107-7.5825-7.1309-9.8594-9.5834"
    " 0 0-1.7904-1.9212-2.1501-2.3075s-0.52935-0.4864-1.1016-0.4864c-1.6228 0-4.3206 0.0041-5.4836 0.0041-0.28205 0-0.45373 0.3433-0.26365 0.562z"
    "m40.997-3.0922c-2.3279-2.4015-14.922-15.392-17.282-17.266-0.8237-0.66016-1.8844-1.1445-3.2149-1.1445-1.3326 0-2.3933 0.48438-3.2149 1.1445"
    "-2.3607 1.8742-14.957 14.865-17.285 17.266-0.19007 0.2187-0.018394 0.562 0.26774 0.562 1.1629 0 3.8587 0.0041 5.4836 0.0041"
    " 0.57227 0 0.7419-0.1001 1.1016-0.4864 0.35972-0.3863 2.1501-2.3075 2.1501-2.3075 2.2768-2.4546 7.8299-7.8748 9.8594-9.5834"
    " 0.7623-0.64176 1.6248-0.62132 1.6371-0.62132 0.0122 0 0.8747-0.02044 1.6371 0.62132 2.0295 1.7107 7.5825 7.1308 9.8593 9.5834"
    " 0 0 1.7904 1.9212 2.1501 2.3075s0.5294 0.4864 1.1016 0.4864c1.6228 0 4.3207-0.0041 5.4836-0.0041 0.2861 0.0021 0.4558-0.3433 0.2657-0.562z"
)


def draw_rivian_compass_logo(draw: ImageDraw.ImageDraw, x: int, y: int, size: int = 48, fill: int = 0) -> None:
    """Renders the official Rivian Compass Logo from its exact SVG vector geometry at 4x supersampling."""
    import re
    tokens = re.findall(r"[a-zA-Z]|[-+]?\d*\.?\d+", RIVIAN_COMPASS_SVG_PATH)
    polys = []
    cur = []
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    last_ctrl = None
    i = 0
    cmd = ""
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        if cmd == "m":
            cx += float(tokens[i])
            cy += float(tokens[i + 1])
            i += 2
            sx, sy = cx, cy
            cur = [(cx, cy)]
            cmd = "l"
        elif cmd == "l":
            cx += float(tokens[i])
            cy += float(tokens[i + 1])
            i += 2
            cur.append((cx, cy))
        elif cmd == "v":
            cy += float(tokens[i])
            i += 1
            cur.append((cx, cy))
        elif cmd == "h":
            cx += float(tokens[i])
            i += 1
            cur.append((cx, cy))
        elif cmd == "c":
            x1, y1 = cx + float(tokens[i]), cy + float(tokens[i + 1])
            x2, y2 = cx + float(tokens[i + 2]), cy + float(tokens[i + 3])
            x3, y3 = cx + float(tokens[i + 4]), cy + float(tokens[i + 5])
            i += 6
            for step in range(1, 9):
                t = step / 8.0
                u = 1.0 - t
                px = u**3 * cx + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3
                py = u**3 * cy + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3
                cur.append((px, py))
            cx, cy = x3, y3
            last_ctrl = (x2, y2)
        elif cmd == "s":
            x1 = 2 * cx - last_ctrl[0] if last_ctrl else cx
            y1 = 2 * cy - last_ctrl[1] if last_ctrl else cy
            x2, y2 = cx + float(tokens[i]), cy + float(tokens[i + 1])
            x3, y3 = cx + float(tokens[i + 2]), cy + float(tokens[i + 3])
            i += 4
            for step in range(1, 9):
                t = step / 8.0
                u = 1.0 - t
                px = u**3 * cx + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3
                py = u**3 * cy + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3
                cur.append((px, py))
            cx, cy = x3, y3
            last_ctrl = (x2, y2)
        elif cmd in ("z", "Z"):
            polys.append(cur)
            cur = []
            cx, cy = sx, sy

    scale = 4
    hi_size = size * scale
    hi = Image.new("L", (hi_size, hi_size), 0)
    hd = ImageDraw.Draw(hi)
    # Original SVG viewBox for compass is 41.16 wide x 40.00 tall
    factor = (hi_size - 2 * scale) / 41.16
    off_x = 1 * scale
    off_y = (hi_size - 40.00 * factor) / 2.0
    for poly in polys:
        pts = [(off_x + px * factor, off_y + py * factor) for px, py in poly]
        hd.polygon(pts, fill=255)
    lo = hi.resize((size, size), resample=Image.Resampling.LANCZOS)
    draw._image.paste(fill, (x, y), lo)


def draw_inkplate_battery_status(
    draw: ImageDraw.ImageDraw,
    right_x: int,
    y: int,
    pct: int,
    font,
    colors: dict,
    draw_fill: bool = True,
) -> None:
    """
    Draws right-aligned 'INKPLATE 10' followed by a small cell-phone-style battery icon
    ending at right_x (1164) below the ground line.
    When draw_fill=False (for static base_blueprint.png), leaves the interior of the battery
    icon blank so the ESP32 can fill it dynamically from display.read_battery().
    """
    bw = 32
    bh = 16
    nub_w = 4
    bx = right_x - (bw + nub_w)  # 1164 - 36 = 1128
    by = y                       # 748 (midpoint 756)

    label = "INKPLATE BATT:"
    bbox = font.getbbox(label)
    lbl_w = bbox[2] - bbox[0]
    draw_crisp_text(draw, (bx - 10 - lbl_w, y + 1), label, font, fill=colors["dark_gray"])

    draw.rectangle([bx, by, bx + bw, by + bh], outline=colors["black"], fill=colors["white"], width=2)
    # Positive terminal nub on right
    draw.rectangle([bx + bw + 1, by + 4, bx + bw + nub_w, by + 12], outline=colors["black"], fill=colors["black"], width=1)
    if draw_fill:
        inner_pad = 3
        inner_w = bw - inner_pad * 2  # 26px
        inner_h = bh - inner_pad * 2  # 10px
        fill_w = max(0, min(inner_w, int(round(inner_w * (pct / 100.0)))))
        if fill_w > 0:
            draw.rectangle(
                [bx + inner_pad, by + inner_pad, bx + inner_pad + fill_w, by + inner_pad + inner_h],
                fill=colors["black"],
            )


def draw_aa_polyline(draw: ImageDraw.ImageDraw, points: list, fill: int = 0, width: int = 2) -> None:
    """
    Draws a 4x supersampled anti-aliased polyline onto draw._image so diagonal
    leader lines have smooth grayscale edges instead of stair-stepped jaggies.
    """
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0 = max(0, min(xs) - width - 4)
    y0 = max(0, min(ys) - width - 4)
    x1 = min(draw._image.width, max(xs) + width + 4)
    y1 = min(draw._image.height, max(ys) + width + 4)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    scale = 4
    hi = Image.new("L", (w * scale, h * scale), 0)
    hd = ImageDraw.Draw(hi)
    scaled_pts = [((px - x0) * scale, (py - y0) * scale) for px, py in points]
    hd.line(scaled_pts, fill=255, width=width * scale)
    lo = hi.resize((w, h), resample=Image.Resampling.LANCZOS)
    draw._image.paste(fill, (x0, y0), lo)


def draw_blueprint_frame(draw: ImageDraw.ImageDraw, colors: dict, cfg: dict):
    """Draws technical architectural border and corner crosshairs."""
    if not cfg["enabled"]:
        return

    mx = cfg["margin_x"]
    my = cfg["margin_y"]
    w = CONFIG["canvas_width"] - mx
    h = CONFIG["canvas_height"] - my
    cs = cfg["corner_cross_size"]

    # Outer border line
    draw.rectangle([mx, my, w, h], outline=colors["mid_gray"], width=cfg["border_width"])

    # Corner crosshairs (+)
    corners = [(mx, my), (w, my), (mx, h), (w, h)]
    for cx, cy in corners:
        draw.line([(cx - cs, cy), (cx + cs, cy)], fill=colors["dark_gray"], width=2)
        draw.line([(cx, cy - cs), (cx, cy + cs)], fill=colors["dark_gray"], width=2)


def draw_header(draw: ImageDraw.ImageDraw, data: dict, colors: dict, cfg: dict):
    """Renders the top branding header (with Rivian Compass Logo) and architectural engineering title block."""
    font_cfg = CONFIG.get("fonts", {})
    font_title = get_architect_font(font_cfg.get("title", 34), bold=True)
    font_sub = get_architect_font(font_cfg.get("subtitle", 19), bold=True)
    font_meta = get_architect_font(font_cfg.get("meta_drawing", 20), bold=True)
    font_meta_sub = get_architect_font(font_cfg.get("meta_status", 19), bold=True)

    # Rivian Compass Logo on far left of header
    logo_x = cfg.get("logo_x", 36)
    logo_y = cfg.get("logo_y", 46)
    logo_sz = cfg.get("logo_size", 48)
    draw_rivian_compass_logo(draw, logo_x, logo_y, size=logo_sz, fill=colors["black"])

    # Left: Vehicle Title & Architectural Subtitle
    draw_crisp_text(draw, (cfg["title_x"], cfg["title_y"]), data["vehicleName"], font_title, fill=colors["black"])
    draw_crisp_text(draw, (cfg["title_x"], cfg["subtitle_y"]), data["blueprintSubtitle"], font_sub, fill=colors["dark_gray"])

    # Right: Architectural Title Block Metadata (Right-aligned)
    meta_line1 = f"{data['drawingNumber']}  /  {data['revision']}"
    bbox1 = font_meta.getbbox(meta_line1)
    w1 = bbox1[2] - bbox1[0]
    draw_crisp_text(draw, (cfg["meta_right_x"] - w1, cfg["meta_y1"]), meta_line1, font_meta, fill=colors["black"])

    status_up = str(data.get("vehicleStatus") or "ONLINE").upper()
    if status_up == "OFFLINE":
        prefix_str = "STATUS: "
        suffix_str = f"  •  SYNC: {data['lastUpdated']}"
        w_prefix = int(round(font_meta_sub.getlength(prefix_str)))
        w_status = int(round(font_meta_sub.getlength(status_up)))
        w_suffix = int(round(font_meta_sub.getlength(suffix_str)))
        pad_x = 5
        total_w = w_prefix + w_status + pad_x * 2 + w_suffix
        x_cur = cfg["meta_right_x"] - total_w
        draw_crisp_text(draw, (x_cur, cfg["meta_y2"]), prefix_str, font_meta_sub, fill=colors["dark_gray"])
        x_cur += w_prefix + pad_x
        draw_reversed_badge(draw, (x_cur, cfg["meta_y2"]), status_up, font_meta_sub, pad_x=pad_x, pad_y=3)
        x_cur += w_status + pad_x
        draw_crisp_text(draw, (x_cur, cfg["meta_y2"]), suffix_str, font_meta_sub, fill=colors["dark_gray"])
    else:
        meta_line2 = f"STATUS: {status_up}  •  SYNC: {data['lastUpdated']}"
        bbox2 = font_meta_sub.getbbox(meta_line2)
        w2 = bbox2[2] - bbox2[0]
        draw_crisp_text(draw, (cfg["meta_right_x"] - w2, cfg["meta_y2"]), meta_line2, font_meta_sub, fill=colors["dark_gray"])

    # Header divider rule spanning full width from logo_x to meta_right_x
    draw.line([(logo_x, cfg["divider_y"]), (cfg["meta_right_x"], cfg["divider_y"])], fill=colors["mid_gray"], width=2)


def draw_battery_card_contents(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, data: dict, colors: dict):
    """
    Renders the contents of Card 1: 01 // BATTERY LEVEL
    Includes hero percentage (78%), an inline battery bargraph,
    and estimated range (EST. RANGE: 264 MI).
    """
    pct = max(0, min(100, int(data["batteryLevel"])))
    range_mi = data["distanceToEmpty"]

    font_cfg = CONFIG.get("fonts", {})
    font_val = get_architect_font(font_cfg.get("card_value", 26), bold=True)
    font_sub = get_architect_font(font_cfg.get("card_sub", 18), bold=True)

    # 1. Hero Percentage (Left side of middle row; reversed badge if < 15%)
    pct_str = f"{pct}%"
    if pct < 15:
        draw_reversed_badge(draw, (cx + 10, cy + 46), pct_str, font_val, pad_x=5, pad_y=3)
    else:
        draw_crisp_text(draw, (cx + 10, cy + 46), pct_str, font_val, fill=colors["black"])

    # 2. Inline Battery Bargraph (Right side of middle row)
    bar_x = cx + 82
    bar_y = cy + 46
    bar_w = w - 102  # 114px wide
    bar_h = 20       # 20px high
    bw = 2

    # Outer bar rectangle
    draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=colors["black"], fill=colors["white"], width=bw)

    # Terminal cap on right
    cap_w = 5
    cap_h = 10
    cap_y = bar_y + (bar_h - cap_h) // 2
    draw.rectangle([bar_x + bar_w + 1, cap_y, bar_x + bar_w + cap_w, cap_y + cap_h], outline=colors["black"], fill=colors["black"], width=1)

    # Proportional inner fill (0 to 100%)
    inner_pad = 3
    inner_w = bar_w - (inner_pad * 2)
    inner_h = bar_h - (inner_pad * 2)
    fill_w = int(inner_w * (pct / 100.0))

    if fill_w > 0:
        fill_x0 = bar_x + inner_pad
        fill_y0 = bar_y + inner_pad
        draw.rectangle([fill_x0, fill_y0, fill_x0 + fill_w, fill_y0 + inner_h], fill=colors["black"])

    # Scale ticks at 25%, 50%, 75% (white inside filled area, black inside unfilled area)
    for tick_pct in (0.25, 0.50, 0.75):
        tx = bar_x + int(bar_w * tick_pct)
        tick_color = colors["white"] if tx <= (bar_x + inner_pad + fill_w) else colors["black"]
        draw.line([(tx, bar_y + inner_pad), (tx, bar_y + bar_h - inner_pad)], fill=tick_color, width=2)

    # 3. Subtitle / Range Remaining
    sub_str = f"EST. RANGE: {range_mi} MI"
    draw_crisp_text(draw, (cx + 10, cy + 88), sub_str, font_sub, fill=colors["dark_gray"])


def draw_battery_state_card_contents(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, data: dict, colors: dict):
    """
    Renders the contents of Card 4: 04 // BATTERY STATE
    Displays usable HV pack capacity as the hero value on the left of the
    middle row (e.g., 90.9 kWh), a vertical drafting divider, and the pack
    temperature on the right (70°F), with 12V status below.
    """
    cap_kwh = float(data.get("batteryCapacity") or 90.9)
    avg_c = float(data.get("batteryTemperature") or 21.6)
    avg_f = round((avg_c * 9.0 / 5.0) + 32.0)

    font_cfg = CONFIG.get("fonts", {})
    font_val = get_architect_font(font_cfg.get("card_value", 26), bold=True)
    font_temp = get_architect_font(font_cfg.get("meta_drawing", 20), bold=True)
    font_sub = get_architect_font(font_cfg.get("card_sub", 18), bold=True)

    # 1. Hero Usable Pack Capacity (Left side of middle row)
    cap_str = f"{cap_kwh:.1f} kWh"
    draw_crisp_text(draw, (cx + 10, cy + 46), cap_str, font_val, fill=colors["black"])

    # 2. Vertical drafting tick + Inline Pack Temperature (Right side of middle row)
    div_x = cx + 128
    draw.line([(div_x, cy + 44), (div_x, cy + 68)], fill=colors["mid_gray"], width=2)
    temp_str = f"{avg_f}°F"
    draw_crisp_text(draw, (div_x + 10, cy + 49), temp_str, font_temp, fill=colors["dark_gray"])

    # 3. Subtitle / 12V System Health
    sub_str = f"12V STATUS: {data['battery12VState']}"
    draw_crisp_text(draw, (cx + 10, cy + 88), sub_str, font_sub, fill=colors["dark_gray"])


def draw_metric_cards(draw: ImageDraw.ImageDraw, data: dict, colors: dict, cfg: dict, dots_cfg: dict):
    """
    Renders 5 technical architectural metric cards across the top margin
    in the requested streamlined sequence:
    1. 01 // BATTERY LEVEL (integrated bargraph & est. range) - No line
    2. 02 // ODOMETER      - Points to behind the dash
    3. 03 // CLIMATE       - Points to middle of driver's window
    4. 04 // BATTERY STATE - Points under rear seat cushion
    5. 05 // CHARGER       - Points to charger door
    """
    start_x = cfg["start_x"]
    y = cfg["y"]
    w = cfg["card_width"]
    h = cfg["card_height"]
    gap = cfg["card_gap"]
    bw = cfg["border_width"]

    # Temperature conversion: Celsius to Fahrenheit
    temp_c = data["cabinClimateInteriorTemperature"]
    temp_f = round((temp_c * 9.0 / 5.0) + 32.0)

    ext_c = data.get("cabinClimateExteriorTemperature")
    if ext_c is not None:
        ext_f = round((float(ext_c) * 9.0 / 5.0) + 32.0)
        climate_sub = f"EXTERIOR: {ext_f}°F"
    else:
        climate_sub = f"RAW: {temp_c:.1f}°C INTERIOR"

    batt_temp_c = data.get("batteryTemperature", 21.6)
    batt_temp_f = round((batt_temp_c * 9.0 / 5.0) + 32.0)

    raw_charger = str(data.get("chargerState") or "DISCONNECTED").lower()
    batt_lim = data.get("batteryLimit")
    lim_int = int(round(float(batt_lim))) if batt_lim is not None else None
    sched_start = data.get("scheduleStartTime")
    tte = data.get("timeToEndOfCharge")

    if raw_charger == "charging_ready":
        charger_hero = "DISCONNECTED"
        charger_sub = "CHARGE PORT READY"
    elif raw_charger == "charging_active":
        charger_hero = "CHARGING"
        rem_m = int(round(float(tte))) if tte is not None else 0
        if rem_m > 0:
            rem_str = (
                f"{rem_m // 60}H {rem_m % 60}M LEFT"
                if (rem_m >= 60 and rem_m % 60 > 0)
                else (f"{rem_m // 60}H LEFT" if rem_m >= 60 else f"{rem_m}M LEFT")
            )
            charger_sub = f"TO {lim_int}% | {rem_str}" if lim_int else rem_str
        elif lim_int:
            charger_sub = f"TO {lim_int}% | ACTIVE"
        else:
            charger_sub = "ACTIVE SESSION"
    elif raw_charger == "charging_complete":
        charger_hero = "COMPLETE"
        charger_sub = f"TARGET {lim_int}% REACHED" if lim_int else "TARGET CHARGE REACHED"
    elif raw_charger == "charging_scheduled":
        charger_hero = "SCHEDULED"
        if sched_start is not None:
            sm = int(sched_start) % 1440
            h24, m = sm // 60, sm % 60
            ampm = "AM" if h24 < 12 else "PM"
            h12 = (h24 % 12) or 12
            start_str = f"{h12}:{m:02d} {ampm}"
            charger_sub = f"STARTS {start_str} (TO {lim_int}%)" if lim_int else f"STARTS AT {start_str}"
        elif lim_int:
            charger_sub = f"SCHEDULED (TO {lim_int}%)"
        else:
            charger_sub = "WAITING FOR SCHEDULE"
    elif raw_charger == "charging_connecting":
        charger_hero = "CONNECTING"
        charger_sub = "HANDSHAKING WITH EVSE"
    else:
        charger_hero = str(data.get("chargerState") or "DISCONNECTED").upper().replace("_", " ")
        charger_sub = "CHARGE PORT READY"

    # Cards list: (dot_key, label, hero_val, subtitle)
    cards = [
        (None,              "01 // BATTERY LEVEL", f"{data['batteryLevel']}%",            f"EST. RANGE: {data['distanceToEmpty']} MI"),
        ("odometer",        "02 // ODOMETER",      f"{data['vehicleMileage']:,} MI",       "LIFETIME TOTAL MILES"),
        ("climate",         "03 // CLIMATE",       f"{temp_f}°F",                           climate_sub),
        ("battery12v",      "04 // BATTERY STATE", f"{batt_temp_f}°F",                      f"12V STATUS: {data['battery12VState']}"),
        ("charger",         "05 // CHARGER",       charger_hero,                            charger_sub),
    ]
    # Front charge port (e.g. R1T): swap the first and last cards and renumber them
    battery_idx = 0
    if CONFIG.get("charger_card_first", False):
        battery_idx = 4
        cards[0], cards[4] = (
            ("charger", "01 // CHARGER", charger_hero, charger_sub),
            (None, "05 // BATTERY LEVEL", cards[0][2], cards[0][3]),
        )

    font_cfg = CONFIG.get("fonts", {})
    font_sec = get_architect_font(font_cfg.get("card_header", 18), bold=True)
    font_val = get_architect_font(font_cfg.get("card_value", 26), bold=True)
    font_sub = get_architect_font(font_cfg.get("card_sub", 18), bold=True)
    font_sub_sm = get_architect_font(17, bold=True)

    card_bottom_y = y + h
    dot_r = cfg.get("terminal_dot_radius", 4)
    lw = cfg.get("card_leader_width", 2)
    shoulder = cfg.get("shoulder_length", 14)

    # 1. Render anti-aliased leader lines from cards down to vehicle components
    if cfg.get("draw_card_leaders", True):
        for i, (dot_key, label, val_str, sub_str) in enumerate(cards):
            if dot_key and dot_key in dots_cfg:
                card_x = start_x + i * (w + gap)
                center_x = card_x + (w // 2)
                tx, ty = dots_cfg[dot_key]

                p0 = (center_x, card_bottom_y)
                p1 = (center_x, card_bottom_y + shoulder)
                p2 = (tx, ty)

                draw_aa_polyline(draw, [p0, p1, p2], fill=colors["black"], width=lw)
                draw.ellipse([tx - dot_r, ty - dot_r, tx + dot_r, ty + dot_r], fill=colors["black"])

    # 2. Render Card Boxes and Text
    for i, (dot_key, label, val_str, sub_str) in enumerate(cards):
        cx = start_x + i * (w + gap)

        # Card container box (Solid white fill so lines don't bleed through)
        draw.rectangle([cx, y, cx + w, y + h], outline=colors["mid_gray"], fill=colors["white"], width=bw)

        # Architectural corner cross marks (+)
        if cfg.get("show_corner_ticks", True):
            t_len = 5
            draw.line([(cx - t_len, y), (cx + t_len, y)], fill=colors["black"], width=2)
            draw.line([(cx, y - t_len), (cx, y + t_len)], fill=colors["black"], width=2)
            draw.line([(cx + w - t_len, y), (cx + w + t_len, y)], fill=colors["black"], width=2)
            draw.line([(cx + w, y - t_len), (cx + w, y + t_len)], fill=colors["black"], width=2)

        # Header accent separator
        draw.line([(cx + 8, y + 34), (cx + w - 8, y + 34)], fill=colors["light_gray"], width=2)

        # Category label
        draw_crisp_text(draw, (cx + 10, y + 8), label, font_sec, fill=colors["dark_gray"])

        # Card-specific rendering: Card 1 has inline bargraph; Card 4 has inline avg + min/max temps
        if i == battery_idx:
            draw_battery_card_contents(draw, cx, y, w, h, data, colors)
        elif i == 3:
            draw_battery_state_card_contents(draw, cx, y, w, h, data, colors)
        else:
            # Standard cards (Odometer, Climate, Charger)
            draw_crisp_text(draw, (cx + 10, y + 46), val_str, font_val, fill=colors["black"])
            use_sub_font = font_sub_sm if font_sub.getlength(sub_str) > (w - 20) else font_sub
            draw_crisp_text(draw, (cx + 10, y + 88), sub_str, use_sub_font, fill=colors["dark_gray"])


def draw_wireframe(canvas: Image.Image, wireframe_path: str, colors: dict, cfg: dict):
    """
    Loads r2_wireframe.png and positions it in the lower half of the canvas.
    Applies 4x-supersampled stroke emboldening and contrast boost for bold, smooth e-paper lines.
    """
    if not os.path.exists(wireframe_path):
        print(f"Warning: Wireframe file '{wireframe_path}' not found! Skipping base image.")
        return

    x, y = cfg["x"], cfg["y"]
    wf_img = Image.open(wireframe_path)

    if cfg["high_contrast"] and "A" in wf_img.mode:
        alpha = wf_img.split()[3]
        w, h = alpha.size
        a_hi = alpha.resize((w * 4, h * 4), resample=Image.Resampling.BICUBIC)
        a_dil = a_hi.filter(ImageFilter.MaxFilter(3))
        a_lo = a_dil.resize((w, h), resample=Image.Resampling.LANCZOS)
        if CONFIG.get("eink_panel_mode", False):
            wf_rendered = Image.eval(
                a_lo,
                lambda a: 255 - min(255, int((a / 255.0) ** 0.65 * 255 * 1.25)),
            )
        else:
            wf_rendered = Image.eval(
                a_lo,
                lambda a: 255 - int(((a / 255.0) ** 0.78) * 255),
            )
    else:
        wf_white = Image.new("RGBA", wf_img.size, (255, 255, 255, 255))
        wf_white.paste(wf_img, (0, 0), wf_img if "A" in wf_img.mode else None)
        wf_rendered = wf_white.convert("L")

    # Paste onto canvas
    canvas.paste(wf_rendered, (x, y))


def draw_tire_pressures(draw: ImageDraw.ImageDraw, data: dict, colors: dict, cfg: dict, dots_cfg: dict):
    """
    Renders text for all 4 tire pressures placed under the car,
    in the white space between the tires above the ground line.
    Features anti-aliased architectural leader lines connecting each badge to the wheels.
    """
    bw = cfg["badge_width"]
    bh = cfg["badge_height"]
    dot_r = cfg.get("terminal_dot_radius", 4)
    lw = cfg.get("leader_line_width", 2)

    font_cfg = CONFIG.get("fonts", {})
    font_lbl = get_architect_font(font_cfg.get("tire_label", 18), bold=True)
    font_val = get_architect_font(font_cfg.get("tire_value", 22), bold=True)

    tires = [
        ("tire_fl", cfg["front_left"],  data["tirePressureFrontLeft"]),
        ("tire_fr", cfg["front_right"], data["tirePressureFrontRight"]),
        ("tire_rl", cfg["rear_left"],   data["tirePressureRearLeft"]),
        ("tire_rr", cfg["rear_right"],  data["tirePressureRearRight"]),
    ]

    # 1. Draw Anti-Aliased Architectural Leader Lines first (so badges sit cleanly on top)
    if cfg.get("draw_leader_lines", True):
        # Front Left: badge -> mid -> target
        fl_cfg = cfg["front_left"]
        fl_target = dots_cfg.get("tire_fl", (300, 641))
        draw_aa_polyline(draw, [fl_cfg["leader_start"], fl_cfg["leader_mid"], fl_target], fill=colors["black"], width=lw)
        draw.ellipse([fl_target[0] - dot_r, fl_target[1] - dot_r, fl_target[0] + dot_r, fl_target[1] + dot_r], fill=colors["black"])

        # Front Right: badge -> mid1 -> mid2 -> target
        fr_cfg = cfg["front_right"]
        fr_target = dots_cfg.get("tire_fr", (305, 681))
        draw_aa_polyline(draw, [fr_cfg["leader_start"], fr_cfg["leader_mid1"], fr_cfg["leader_mid2"], fr_target], fill=colors["black"], width=lw)
        draw.ellipse([fr_target[0] - dot_r, fr_target[1] - dot_r, fr_target[0] + dot_r, fr_target[1] + dot_r], fill=colors["black"])

        # Rear Left: badge -> mid1 -> mid2 -> target
        rl_cfg = cfg["rear_left"]
        rl_target = dots_cfg.get("tire_rl", (875, 681))
        draw_aa_polyline(draw, [rl_cfg["leader_start"], rl_cfg["leader_mid1"], rl_cfg["leader_mid2"], rl_target], fill=colors["black"], width=lw)
        draw.ellipse([rl_target[0] - dot_r, rl_target[1] - dot_r, rl_target[0] + dot_r, rl_target[1] + dot_r], fill=colors["black"])

        # Rear Right: badge -> mid -> target
        rr_cfg = cfg["rear_right"]
        rr_target = dots_cfg.get("tire_rr", (830, 641))
        draw_aa_polyline(draw, [rr_cfg["leader_start"], rr_cfg["leader_mid"], rr_target], fill=colors["black"], width=lw)
        draw.ellipse([rr_target[0] - dot_r, rr_target[1] - dot_r, rr_target[0] + dot_r, rr_target[1] + dot_r], fill=colors["black"])

    # 2. Draw Badges in the underbody space
    for dot_key, pos, psi in tires:
        bx = pos["x"]
        by = pos["y"]
        lbl = pos["label"]
        val_str = f"{psi:.0f} PSI"

        # Badge container box (Solid white fill to mask background cleanly)
        draw.rectangle([bx, by, bx + bw, by + bh], fill=colors["white"], outline=colors["black"], width=2)

        # Divider line inside badge
        draw.line([(bx + 6, by + 25), (bx + bw - 6, by + 25)], fill=colors["light_gray"], width=2)

        # Label (Front L, Front R, Rear L, Rear R)
        draw_crisp_text(draw, (bx + 10, by + 4), lbl, font_lbl, fill=colors["dark_gray"])

        # PSI value (reversed badge if low tire < 32 PSI)
        if float(psi) < 32.0:
            draw_reversed_badge(draw, (bx + 10, by + 30), val_str, font_val, pad_x=5, pad_y=3, box_w=bw - 10)
        else:
            draw_crisp_text(draw, (bx + 10, by + 30), val_str, font_val, fill=colors["black"])

    # 3. Vertical center datum tick between front and rear axle pairs
    mid_x = (cfg["front_right"]["x"] + bw + cfg["rear_left"]["x"]) // 2
    by = cfg["front_left"]["y"]
    draw.line([(mid_x, by + 4), (mid_x, by + bh - 4)], fill=colors["mid_gray"], width=2)


# ==============================================================================
# 5. LIVE TELEMETRY FETCHER INTEGRATION
# ==============================================================================
def update_telemetry_from_live_api(data: dict) -> None:
    """
    Fetches live R2 telemetry via Rivian's GraphQL + Parallax WebSocket APIs
    and updates the TELEMETRY dictionary in-place.
    """
    config = load_config()
    with requests.Session() as session:
        headers = authenticate_with_refresh_token(session, config)
        vehicle_id, vehicle_desc, born_on_date = get_r2_vehicle_and_born_on_date(
            session, headers, config
        )
        live = fetch_vehicle_telemetry(
            session,
            headers,
            vehicle_id,
            user_session_token=config.get("user_session_token"),
        )

    now_str = datetime.now().strftime("TODAY  •  %-I:%M %p")
    data["lastUpdated"] = now_str
    data["vehicleStatus"] = "ONLINE"
    if born_on_date:
        data["actualGeneralAssemblyDate"] = born_on_date

    if live.get("batteryLevel") is not None:
        data["batteryLevel"] = round(float(live["batteryLevel"]))
    if live.get("batteryLimit") is not None:
        data["batteryLimit"] = round(float(live["batteryLimit"]))
    if live.get("batteryCapacity") is not None:
        data["batteryCapacity"] = round(float(live["batteryCapacity"]), 1)
    if live.get("distanceToEmpty") is not None:
        data["distanceToEmpty"] = round(float(live["distanceToEmpty"]) * KM_TO_MILES)
    if live.get("vehicleMileage") is not None:
        data["vehicleMileage"] = round(float(live["vehicleMileage"]) * METERS_TO_MILES)
    if live.get("chargerState") is not None:
        data["chargerState"] = str(live["chargerState"])
    if live.get("timeToEndOfCharge") is not None:
        data["timeToEndOfCharge"] = int(round(float(live["timeToEndOfCharge"])))
    if live.get("scheduleStartTime") is not None:
        data["scheduleStartTime"] = int(round(float(live["scheduleStartTime"])))
    if live.get("battery12VState") is not None:
        data["battery12VState"] = str(live["battery12VState"]).upper()
    if live.get("batteryTemperature") is not None:
        data["batteryTemperature"] = float(live["batteryTemperature"])
    if live.get("batteryTempMin") is not None:
        data["batteryTempMin"] = float(live["batteryTempMin"])
    if live.get("batteryTempMax") is not None:
        data["batteryTempMax"] = float(live["batteryTempMax"])
    if live.get("cabinClimateInteriorTemperature") is not None:
        data["cabinClimateInteriorTemperature"] = float(
            live["cabinClimateInteriorTemperature"]
        )
    if live.get("cabinClimateExteriorTemperature") is not None:
        data["cabinClimateExteriorTemperature"] = float(
            live["cabinClimateExteriorTemperature"]
        )

    for tire_key in (
        "tirePressureFrontLeft",
        "tirePressureFrontRight",
        "tirePressureRearLeft",
        "tirePressureRearRight",
    ):
        if isinstance(live.get(tire_key), (int, float)):
            data[tire_key] = float(live[tire_key])


# ==============================================================================
# 6. MAIN ENTRY POINT
# ==============================================================================
def main():
    w = CONFIG["canvas_width"]
    h = CONFIG["canvas_height"]
    colors = CONFIG["colors"]
    layout = CONFIG["layout"]
    dots = CONFIG["terminal_dots"]
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outfile = CONFIG["output_file"]
    if not os.path.isabs(outfile):
        outfile = os.path.join(repo_root, outfile)
    os.makedirs(os.path.dirname(outfile), exist_ok=True)
    wf_path = CONFIG["wireframe_file"]
    if not os.path.isabs(wf_path):
        wf_path = os.path.join(repo_root, wf_path)

    print("=" * 65)
    print(f"Rivian Living Art Frame - Architectural Blueprint Generator ({TELEMETRY['vehicleName']})")
    print(f"Canvas: {w}x{h} (Inkplate 10 Grayscale) | Font: Neucha")
    print("=" * 65)

    # Fetch live telemetry from Rivian API
    print("[0/6] Fetching live telemetry from Rivian GraphQL + Parallax API...")
    try:
        update_telemetry_from_live_api(TELEMETRY)
        print(
            f"      Live data synced ({TELEMETRY['batteryLevel']}% SOC, "
            f"{TELEMETRY['distanceToEmpty']} mi, {TELEMETRY['vehicleMileage']:,} mi odometer)."
        )
    except Exception as exc:
        print(f"      Warning: Live telemetry fetch failed ({exc}). Using fallback values.")
        TELEMETRY["vehicleStatus"] = "OFFLINE"

    # Initialize 8-bit Grayscale Canvas ('L' mode: 0=black, 255=white)
    canvas = Image.new("L", (w, h), colors["white"])
    draw = ImageDraw.Draw(canvas)

    # Step 1: Architectural Outer Blueprint Frame & Corner Crosshairs
    print("[1/6] Rendering architectural blueprint border & crosshairs...")
    draw_blueprint_frame(draw, colors, layout["blueprint_frame"])

    # Step 2: Base Wireframe Image (Positioned vertically near center)
    print(f"[2/6] Rendering wireframe base from '{wf_path}' at Y={layout['wireframe']['y']}...")
    draw_wireframe(canvas, wf_path, colors, layout["wireframe"])

    # Step 3: Datum Ground Line, Left Edition Footer, & Right Inkplate 10 Battery Status
    ground_y = layout["wireframe"].get("ground_y", 740)
    draw.line([(35, ground_y), (1165, ground_y)], fill=colors["mid_gray"], width=2)
    font_cfg = CONFIG.get("fonts", {})
    font_datum = get_architect_font(font_cfg.get("ground_datum", 19), bold=True)
    draw_inkplate_battery_status(draw, 1164, ground_y + 8, pct=85, font=font_datum, colors=colors, draw_fill=True)

    # Blueprint Footer Metadata (Left-aligned) & Configurable Revision
    config_json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    born_on_date = TELEMETRY.get("actualGeneralAssemblyDate", "2026-01-01")
    footer_edition = TELEMETRY["vehicleName"]
    vehicle_name = ""
    if os.path.exists(config_json_path):
        try:
            with open(config_json_path, "r", encoding="utf-8") as cf:
                cfg_data = json.load(cf)
                born_on_date = cfg_data.get("actual_general_assembly_date") or born_on_date
                footer_edition = str(cfg_data.get("footer_edition") or footer_edition).strip().upper()
                vehicle_name = str(cfg_data.get("vehicle_name") or "").strip().upper()
                dwg_cfg = str(cfg_data.get("drawing_number") or "").strip()
                if dwg_cfg:
                    TELEMETRY["drawingNumber"] = dwg_cfg if dwg_cfg.upper().startswith("DWG") else f"DWG NO. {dwg_cfg}"
                rev_cfg = str(cfg_data.get("revision") or "").strip()
                if rev_cfg:
                    TELEMETRY["revision"] = rev_cfg if rev_cfg.upper().startswith("REV") else f"REV {rev_cfg}"
        except Exception:
            pass

    font_footer = get_architect_font(font_cfg.get("ground_footer", 19), bold=True)
    footer_parts = [footer_edition] if footer_edition else []
    if vehicle_name and vehicle_name != footer_edition:
        footer_parts.append(vehicle_name)
    if born_on_date:
        footer_parts.append(f"BORN ON: {born_on_date}")
    footer_text = "  //  ".join(footer_parts)
    draw_crisp_text(draw, (38, ground_y + 9), footer_text, font_footer, fill=colors["dark_gray"])

    # Step 4: Architectural Header & Title Block
    print("[3/6] Rendering architectural header and title block...")
    draw_header(draw, TELEMETRY, colors, layout["header"])

    # Step 5: Metric Cards Grid with Integrated Battery Bar & Component Leader Lines
    print("[4/6] Rendering streamlined metric cards with leader lines to car...")
    draw_metric_cards(draw, TELEMETRY, colors, layout["metrics_grid"], dots)

    # Step 6: Tire Pressure Callouts with Technical Leader Lines
    print("[5/6] Rendering underbody tire pressures with technical leader lines...")
    draw_tire_pressures(draw, TELEMETRY, colors, layout["tire_pressures"], dots)

    # Step 7: Save Image
    print(f"[6/6] Saving output image to '{outfile}'...")
    canvas.save(outfile)

    print("=" * 65)
    print(f"SUCCESS: Blueprint mockup generated at '{outfile}'.")
    print(f"Dimensions: {canvas.size[0]} x {canvas.size[1]} | Mode: {canvas.mode}")
    print("=" * 65)


if __name__ == "__main__":
    main()
