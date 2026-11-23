#!/usr/bin/env python3
"""
================================================================================
Rivian R2 Living Art Frame - Page 2: Telemetry History & Analytics Mockup
Target Display: Inkplate 10 (1200x820 e-paper display, 3-bit GS4 grayscale)
Library: Pillow (PIL)
Typography: Neucha (SIL OFL Architectural Hand-Printed)
================================================================================
"""

import json
import math
import os
from PIL import Image, ImageDraw

from generate_mockup import (
    CONFIG,
    TELEMETRY,
    draw_aa_polyline,
    draw_blueprint_frame,
    draw_crisp_text,
    draw_inkplate_battery_status,
    draw_reversed_badge,
    draw_rivian_compass_logo,
    get_architect_font,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(REPO_ROOT, "local", "previews", "r2_page2_history_mockup.png")


def draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    p1: tuple,
    p2: tuple,
    dash_len: int = 6,
    gap_len: int = 5,
    fill: int = 0,
    width: int = 1,
) -> None:
    """Draws a crisp horizontal, vertical, or diagonal dashed line."""
    x0, y0 = float(p1[0]), float(p1[1])
    x1, y1 = float(p2[0]), float(p2[1])
    dx = x1 - x0
    dy = y1 - y0
    dist = math.hypot(dx, dy)
    if dist <= 0:
        return
    ux, uy = dx / dist, dy / dist
    pos = 0.0
    step = dash_len + gap_len
    while pos < dist:
        end_pos = min(dist, pos + dash_len)
        sx = int(round(x0 + ux * pos))
        sy = int(round(y0 + uy * pos))
        ex = int(round(x0 + ux * end_pos))
        ey = int(round(y0 + uy * end_pos))
        draw.line([(sx, sy), (ex, ey)], fill=fill, width=width)
        pos += step


def draw_panel_box(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    title: str,
    right_tag: str = "",
) -> None:
    """
    Draws an architectural blueprint panel container matching Page 1's metric cards:
    - 2px crisp black border
    - Corner drafting cross-ticks
    - Header title bar with 18px architectural display font and divider rule at y + 34
    """
    draw.rectangle([x, y, x + w, y + h], fill=255, outline=0, width=2)

    # Architectural corner drafting cross-ticks
    t_len = 5
    for tx, ty in [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]:
        draw.line([(tx - t_len, ty), (tx + t_len, ty)], fill=0, width=1)
        draw.line([(tx, ty - t_len), (tx, ty + t_len)], fill=0, width=1)

    font_hdr = get_architect_font(18, bold=True)
    font_tag = get_architect_font(17, bold=True)
    draw_crisp_text(draw, (x + 12, y + 9), title, font_hdr, fill=0)

    if right_tag:
        bbox = font_tag.getbbox(right_tag)
        tw = bbox[2] - bbox[0]
        draw_crisp_text(draw, (x + w - 12 - tw, y + 10), right_tag, font_tag, fill=60)

    # Header divider line
    draw.line([(x + 10, y + 34), (x + w - 10, y + 34)], fill=0, width=1)


def draw_aa_circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: int, fill: int = 0, outline: int = None) -> None:
    """Draws a 4x supersampled circle marker."""
    scale = 4
    pad = 2
    sz = (r * 2 + pad * 2)
    hi = Image.new("L", (sz * scale, sz * scale), 0)
    hd = ImageDraw.Draw(hi)
    hd.ellipse(
        [pad * scale, pad * scale, (pad + r * 2) * scale, (pad + r * 2) * scale],
        fill=255,
    )
    lo = hi.resize((sz, sz), resample=Image.Resampling.LANCZOS)
    ix = int(round(cx)) - r - pad
    iy = int(round(cy)) - r - pad
    draw._image.paste(fill, (ix, iy), lo)
    if outline is not None and r >= 3:
        hi_in = Image.new("L", (sz * scale, sz * scale), 0)
        hd_in = ImageDraw.Draw(hi_in)
        r_in = max(1, r - 2)
        off = (r - r_in)
        hd_in.ellipse(
            [(pad + off) * scale, (pad + off) * scale, (pad + off + r_in * 2) * scale, (pad + off + r_in * 2) * scale],
            fill=255,
        )
        lo_in = hi_in.resize((sz, sz), resample=Image.Resampling.LANCZOS)
        draw._image.paste(outline, (ix, iy), lo_in)


def render_panel_01_soc_timeline(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 01 // STATE OF CHARGE & RANGE TIMELINE (7-Day Dual Time-Series + Charging Bands)
    Visualizes batteryLevel (%) and distanceToEmpty (MI) over 7 days with shaded charging windows.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "01 // STATE OF CHARGE & RANGE TIMELINE",
        "7-DAY LOG  •  30M INTERVAL",
    )

    font_stat = get_architect_font(17, bold=True)
    font_axis = get_architect_font(17, bold=True)

    # Summary KPI strip under panel header
    kpi_y = y + 42
    draw_crisp_text(
        draw,
        (x + 14, kpi_y),
        "NOW: 78% (264 MI)   •   MIN / MAX: 22% / 85%   •   CHARGES: 2 (+118 kWh)",
        font_stat,
        fill=0,
    )
    draw.line([(x + 10, y + 66), (x + w - 10, y + 66)], fill=160, width=1)

    # Chart Plot Area
    plot_x0 = x + 54
    plot_x1 = x + w - 58
    plot_y0 = y + 78
    plot_y1 = y + h - 34
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    # Synthetic realistic 7-day SOC trajectory (every 4 hours = 43 points across 7 days)
    # Day 1 (Thu): 74% -> 58%
    # Day 2 (Fri): 58% -> 31%
    # Day 3 (Sat): 31% -> 22% -> CHARGING SESSION 1 -> 85% -> 76%
    # Day 4 (Sun): 76% -> 52%
    # Day 5 (Mon): 52% -> 38%
    # Day 6 (Tue): 38% -> 26% -> CHARGING SESSION 2 -> 82%
    # Day 7 (Wed): 82% -> 78%
    soc_pts = [
        (0.00, 74), (0.05, 73), (0.09, 66), (0.13, 59),
        (0.17, 58), (0.21, 51), (0.26, 41), (0.30, 32),
        (0.34, 22), (0.39, 85), (0.43, 84), (0.47, 76),
        (0.52, 74), (0.56, 63), (0.60, 52), (0.65, 51),
        (0.69, 44), (0.74, 38), (0.78, 26), (0.83, 82),
        (0.88, 82), (0.94, 79), (1.00, 78),
    ]

    # Charging session intervals (t_start, t_end, label)
    charge_windows = [
        (0.34, 0.39, "+63%"),
        (0.78, 0.83, "+56%"),
    ]

    # 1. Shaded Charging Window Columns (diagonal hatch + light gray fill)
    for t0, t1, chg_lbl in charge_windows:
        cx0 = int(round(plot_x0 + t0 * plot_w))
        cx1 = int(round(plot_x0 + t1 * plot_w))
        draw.rectangle([cx0, plot_y0, cx1, plot_y1], fill=228, outline=150, width=1)
        # Architectural parallel 45-deg hatch lines clipped cleanly to charging window
        hx0, hx1 = cx0 + 2, cx1 - 2
        hy0, hy1 = plot_y0 + 2, plot_y1 - 2
        for k in range(hx0 + hy0 + 6, hx1 + hy1, 10):
            sy0 = k - hx0
            if sy0 > hy1:
                sy0 = hy1
                sx0 = k - hy1
            else:
                sx0 = hx0
            sy1 = k - hx1
            if sy1 < hy0:
                sy1 = hy0
                sx1 = k - hy0
            else:
                sx1 = hx1
            if sx0 <= sx1:
                draw.line([(sx0, sy0), (sx1, sy1)], fill=175, width=1)

    # 2. Horizontal Grid Lines & Left/Right Axis Labels (100%, 75%, 50%, 25%, 0% / Range MI)
    full_range_mi = 338  # 100% SOC equivalent range
    for pct_tick in (100, 75, 50, 25, 0):
        gy = int(round(plot_y1 - (pct_tick / 100.0) * plot_h))
        if pct_tick in (0, 100):
            draw.line([(plot_x0, gy), (plot_x1, gy)], fill=0, width=1)
        else:
            draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=4, gap_len=4, fill=150, width=1)

        # Left Axis: SOC %
        lbl_l = f"{pct_tick}%"
        bbox_l = font_axis.getbbox(lbl_l)
        lw = bbox_l[2] - bbox_l[0]
        draw_crisp_text(draw, (plot_x0 - 6 - lw, gy - 8), lbl_l, font_axis, fill=0)

        # Right Axis: Est. Range MI
        if pct_tick in (100, 50, 0):
            mi_val = int(round(full_range_mi * (pct_tick / 100.0)))
            lbl_r = f"{mi_val}m"
            draw_crisp_text(draw, (plot_x1 + 6, gy - 8), lbl_r, font_axis, fill=50)

    # 3. 15% Low-Battery Warning Threshold Datum Line
    warn_y = int(round(plot_y1 - 0.15 * plot_h))
    draw_dashed_line(draw, (plot_x0, warn_y), (plot_x1, warn_y), dash_len=8, gap_len=4, fill=60, width=1)

    # 4. Area Fill Under SOC Curve (GS4 Light Gray Fill = 212)
    poly_pts = [(plot_x0, plot_y1)]
    curve_xy = []
    for t, soc in soc_pts:
        px = int(round(plot_x0 + t * plot_w))
        py = int(round(plot_y1 - (soc / 100.0) * plot_h))
        curve_xy.append((px, py))
        poly_pts.append((px, py))
    poly_pts.append((plot_x1, plot_y1))
    draw.polygon(poly_pts, fill=215)

    # Re-draw charging band hatch and horizontal grid lines softly over the area fill
    for pct_tick in (75, 50, 25):
        gy = int(round(plot_y1 - (pct_tick / 100.0) * plot_h))
        draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=4, gap_len=4, fill=155, width=1)

    # 5. Bold Anti-Aliased SOC Trajectory Curve & Key Vertex Dots
    draw_aa_polyline(draw, curve_xy, fill=0, width=3)
    for idx, (px, py) in enumerate(curve_xy):
        if idx in (0, 8, 9, 18, 19, len(curve_xy) - 1):
            draw_aa_circle(draw, px, py, r=4, fill=0, outline=255 if idx == len(curve_xy) - 1 else None)

    # Callout badges above the two charging peaks
    for (t0, t1, chg_lbl), peak_idx in zip(charge_windows, (9, 19)):
        px, py = curve_xy[peak_idx]
        draw_crisp_text(draw, (px - 18, py - 20), chg_lbl, font_axis, fill=0)

    # 6. Bottom X-Axis Day Ticks & Labels (THU .. WED)
    days = ["THU", "FRI", "SAT", "SUN", "MON", "TUE", "WED"]
    for i, d_lbl in enumerate(days):
        dx = int(round(plot_x0 + (i / (len(days) - 1)) * plot_w))
        draw.line([(dx, plot_y1), (dx, plot_y1 + 5)], fill=0, width=2)
        bbox_d = font_axis.getbbox(d_lbl)
        dw = bbox_d[2] - bbox_d[0]
        draw_crisp_text(draw, (dx - dw // 2, plot_y1 + 8), d_lbl, font_axis, fill=0)

    # Left & Right Vertical Plot Borders
    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=0, width=2)
    draw.line([(plot_x1, plot_y0), (plot_x1, plot_y1)], fill=0, width=1)


def render_panel_02_daily_mileage(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 02 // DAILY DISTANCE DRIVEN (14-Day Odometer Delta Bar Histogram)
    Visualizes daily miles driven derived from vehicleMileage deltas, with 14-day average line.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "02 // DAILY DISTANCE DRIVEN",
        "14-DAY HISTOGRAM",
    )

    font_stat = get_architect_font(17, bold=True)
    font_axis = get_architect_font(17, bold=True)

    # 14 days of daily mileage
    daily_miles = [28, 34, 19, 64, 52, 14, 31, 29, 38, 86, 44, 12, 24, 36]
    day_letters = ["T", "F", "S", "S", "M", "T", "W", "T", "F", "S", "S", "M", "T", "W"]
    total_14d = sum(daily_miles)
    avg_mi = total_14d / len(daily_miles)
    peak_mi = max(daily_miles)

    # Summary KPI strip
    kpi_y = y + 42
    draw_crisp_text(
        draw,
        (x + 14, kpi_y),
        f"14D TOTAL: {total_14d} MI   •   AVG: {avg_mi:.1f} MI/D   •   PEAK: {peak_mi} MI",
        font_stat,
        fill=0,
    )
    draw.line([(x + 10, y + 66), (x + w - 10, y + 66)], fill=160, width=1)

    # Plot coordinates
    plot_x0 = x + 48
    plot_x1 = x + w - 18
    plot_y0 = y + 80
    plot_y1 = y + h - 34
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0
    max_y_val = 100.0

    # Horizontal grid lines (100, 75, 50, 25, 0 MI)
    for y_tick in (100, 75, 50, 25, 0):
        gy = int(round(plot_y1 - (y_tick / max_y_val) * plot_h))
        if y_tick == 0:
            draw.line([(plot_x0, gy), (plot_x1, gy)], fill=0, width=2)
        else:
            draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=4, gap_len=4, fill=160, width=1)
        if y_tick in (100, 50, 0):
            lbl = f"{y_tick}"
            bbox = font_axis.getbbox(lbl)
            lw = bbox[2] - bbox[0]
            draw_crisp_text(draw, (plot_x0 - 8 - lw, gy - 8), lbl, font_axis, fill=0)

    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=0, width=2)

    # Render 14 vertical histogram bars
    n_bars = len(daily_miles)
    slot_w = plot_w / n_bars
    bar_w = int(slot_w * 0.64)

    for i, (mi, d_ch) in enumerate(zip(daily_miles, day_letters)):
        cx = int(round(plot_x0 + (i + 0.5) * slot_w))
        bx0 = cx - bar_w // 2
        bx1 = bx0 + bar_w
        bh = int(round((mi / max_y_val) * plot_h))
        by0 = plot_y1 - bh

        # Highlight peak day & today in solid black (0), standard days in medium charcoal (95) with 2px black border
        is_peak = (mi == peak_mi)
        is_today = (i == n_bars - 1)
        bar_fill = 0 if (is_peak or is_today) else 120
        draw.rectangle([bx0, by0, bx1, plot_y1], fill=bar_fill, outline=0, width=2)

        # Value label above peak bar
        if is_peak:
            p_str = f"{mi}"
            pb = font_axis.getbbox(p_str)
            pw = pb[2] - pb[0]
            draw_crisp_text(draw, (cx - pw // 2, by0 - 20), p_str, font_axis, fill=0)

        # Day initial along X-axis
        db = font_axis.getbbox(d_ch)
        dw = db[2] - db[0]
        draw_crisp_text(draw, (cx - dw // 2, plot_y1 + 8), d_ch, font_axis, fill=0)

    # 14-Day Average Dashed Datum Line across histogram
    avg_y = int(round(plot_y1 - (avg_mi / max_y_val) * plot_h))
    draw_dashed_line(draw, (plot_x0, avg_y), (plot_x1, avg_y), dash_len=7, gap_len=4, fill=0, width=2)
    # Small white backing tag for AVG label in upper-left open area above bars 1-3
    avg_tag = f"AVG {avg_mi:.0f} MI"
    tb = font_axis.getbbox(avg_tag)
    tw = tb[2] - tb[0]
    tag_x = plot_x0 + 8
    draw.rectangle([tag_x, avg_y - 22, tag_x + tw + 8, avg_y - 4], fill=255, outline=0, width=1)
    draw_crisp_text(draw, (tag_x + 4, avg_y - 20), avg_tag, font_axis, fill=0)


def render_panel_03_thermal_profile(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 03 // 3-ZONE THERMAL PROFILE (7-Day Multi-Line Temperature Chart)
    Compares HV Battery Pack (°F), Cabin Interior (°F), and Exterior Ambient (°F) over 7 days.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "03 // 3-ZONE THERMAL",
        "7-DAY (°F)",
    )

    font_leg = get_architect_font(17, bold=True)
    font_axis = get_architect_font(17, bold=True)

    # Legend bar with line style keys
    leg_y = y + 42
    # Key 1: PACK (Solid thick black)
    draw.line([(x + 14, leg_y + 8), (x + 34, leg_y + 8)], fill=0, width=3)
    draw_crisp_text(draw, (x + 40, leg_y), "PACK 68°", font_leg, fill=0)

    # Key 2: CABIN (Dashed black)
    draw_dashed_line(draw, (x + 132, leg_y + 8), (x + 154, leg_y + 8), dash_len=5, gap_len=3, fill=0, width=2)
    draw_crisp_text(draw, (x + 160, leg_y), "CABIN 70°", font_leg, fill=0)

    # Key 3: EXT (Gray solid line)
    draw.line([(x + 258, leg_y + 8), (x + 278, leg_y + 8)], fill=120, width=2)
    draw_crisp_text(draw, (x + 284, leg_y), "EXT 60°", font_leg, fill=60)

    draw.line([(x + 10, y + 66), (x + w - 10, y + 66)], fill=160, width=1)

    # Plot Area
    plot_x0 = x + 46
    plot_x1 = x + w - 26
    plot_y0 = y + 80
    plot_y1 = y + h - 34
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    min_t, max_t = 40.0, 90.0

    def temp_to_y(tf: float) -> int:
        return int(round(plot_y1 - ((tf - min_t) / (max_t - min_t)) * plot_h))

    # Horizontal grid lines (90°, 65°, 40°F)
    for t_tick in (90, 65, 40):
        gy = temp_to_y(t_tick)
        if t_tick == 40:
            draw.line([(plot_x0, gy), (plot_x1, gy)], fill=0, width=2)
        else:
            draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=4, gap_len=4, fill=165, width=1)
        lbl = f"{t_tick}°"
        bbox = font_axis.getbbox(lbl)
        lw = bbox[2] - bbox[0]
        draw_crisp_text(draw, (plot_x0 - 6 - lw, gy - 8), lbl, font_axis, fill=0)

    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=0, width=2)

    # 7-day trajectories (22 points = morning low / afternoon high / evening over 7 days)
    # Shows a cool weekend front (Sat/Sun lows ~43-45°F) warming back up mid-week
    ext_temps = [
        50, 72, 58,  # Thu
        48, 68, 54,  # Fri
        43, 61, 49,  # Sat (cooler front)
        44, 63, 51,  # Sun
        49, 71, 57,  # Mon
        52, 76, 62,  # Tue
        51, 70, 63, 60,  # Wed
    ]
    cab_temps = [
        58, 71, 64,  # Thu
        56, 70, 62,  # Fri
        52, 68, 59,  # Sat
        53, 69, 60,  # Sun
        57, 71, 64,  # Mon
        59, 72, 66,  # Tue
        58, 71, 69, 70,  # Wed
    ]
    pack_temps = [
        68, 72, 70,  # Thu
        67, 71, 69,  # Fri
        64, 74, 70,  # Sat (charge session warms pack slightly)
        66, 70, 68,  # Sun
        67, 71, 69,  # Mon
        68, 75, 72,  # Tue (charge session 2)
        69, 71, 69, 68,  # Wed
    ]

    n_pts = len(ext_temps)
    ext_xy = [(int(round(plot_x0 + (i / (n_pts - 1)) * plot_w)), temp_to_y(v)) for i, v in enumerate(ext_temps)]
    cab_xy = [(int(round(plot_x0 + (i / (n_pts - 1)) * plot_w)), temp_to_y(v)) for i, v in enumerate(cab_temps)]
    pack_xy = [(int(round(plot_x0 + (i / (n_pts - 1)) * plot_w)), temp_to_y(v)) for i, v in enumerate(pack_temps)]

    # 1. Exterior temperature curve (medium gray 125)
    draw_aa_polyline(draw, ext_xy, fill=125, width=2)

    # 2. Cabin temperature curve (dashed black)
    for i in range(len(cab_xy) - 1):
        draw_dashed_line(draw, cab_xy[i], cab_xy[i + 1], dash_len=5, gap_len=3, fill=0, width=2)

    # 3. HV Pack temperature curve (bold solid black + terminal dot)
    draw_aa_polyline(draw, pack_xy, fill=0, width=3)
    draw_aa_circle(draw, pack_xy[-1][0], pack_xy[-1][1], r=4, fill=0)

    # Bottom X-Axis 7-Day Labels (THU .. WED)
    days_7 = ["THU", "FRI", "SAT", "SUN", "MON", "TUE", "WED"]
    for i, lbl in enumerate(days_7):
        tx = int(round(plot_x0 + (i / (len(days_7) - 1)) * plot_w))
        draw.line([(tx, plot_y1), (tx, plot_y1 + 5)], fill=0, width=2)
        b = font_axis.getbbox(lbl)
        lw = b[2] - b[0]
        draw_crisp_text(draw, (tx - lw // 2, plot_y1 + 8), lbl, font_axis, fill=0)


def render_panel_04_charger_and_efficiency(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 04 // DRIVING EFFICIENCY & PACK HEALTH
    Uses a floating marker + target-deviation stem plot (anchored to the 3.2 MI/kWh
    rated datum rather than a non-zero bar floor) over 7 days, paired with average
    efficiency and HV Pack usable capacity tracking below.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "04 // EFFICIENCY & CAPACITY",
        "7D MI/kWh",
    )

    font_lbl = get_architect_font(17, bold=True)
    font_axis = get_architect_font(17, bold=True)
    font_hero = get_architect_font(24, bold=True)

    # Upper Section: 7-Day Daily Efficiency (MI/kWh) Floating Marker & Deviation Plot
    plot_x0 = x + 46
    plot_x1 = x + w - 18
    plot_y0 = y + 48
    plot_y1 = y + 174
    plot_w = plot_x1 - plot_x0
    plot_h = plot_y1 - plot_y0

    min_eff, max_eff = 1.5, 4.5
    rated_eff = 3.00  # Fixed midpoint at 3.0 MI/kWh matching in-vehicle efficiency graph

    def eff_to_y(val: float) -> int:
        return int(round(plot_y1 - ((val - min_eff) / (max_eff - min_eff)) * plot_h))

    # Y-axis ticks: 4.5, 3.0 (fixed midpoint), 1.5 MI/kWh
    for e_tick in (4.5, 3.0, 1.5):
        gy = eff_to_y(e_tick)
        if e_tick == 1.5:
            draw.line([(plot_x0, gy), (plot_x1, gy)], fill=0, width=2)
        elif e_tick == 3.0:
            draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=6, gap_len=4, fill=0, width=2)
        else:
            draw_dashed_line(draw, (plot_x0, gy), (plot_x1, gy), dash_len=4, gap_len=4, fill=165, width=1)
        lbl = f"{e_tick:.1f}"
        b = font_axis.getbbox(lbl)
        lw = b[2] - b[0]
        draw_crisp_text(draw, (plot_x0 - 6 - lw, gy - 8), lbl, font_axis, fill=0)

    draw.line([(plot_x0, plot_y0), (plot_x0, plot_y1)], fill=0, width=2)

    # 7 days of daily efficiency (MI/kWh)
    eff_days = [
        ("THU", 3.25),
        ("FRI", 2.90),
        ("SAT", 2.45),  # Cooler + highway trip
        ("SUN", 2.80),
        ("MON", 3.40),
        ("TUE", 3.65),  # Warm commuter efficiency peak
        ("WED", 3.20),
    ]
    n_d = len(eff_days)
    slot_w = plot_w / n_d
    r_y = eff_to_y(rated_eff)

    pts_xy = []
    for i, (d_lbl, e_val) in enumerate(eff_days):
        cx = int(round(plot_x0 + (i + 0.5) * slot_w))
        cy = eff_to_y(e_val)
        pts_xy.append((cx, cy, e_val))

        # Subtle vertical day tick on bottom axis
        draw.line([(cx, plot_y1), (cx, plot_y1 + 4)], fill=0, width=2)

        # Vertical deviation stem from the 3.2 MI/kWh rated datum to the floating marker
        draw.line([(cx, r_y), (cx, cy)], fill=90, width=2)

        # Day label under axis
        db = font_axis.getbbox(d_lbl)
        dw = db[2] - db[0]
        draw_crisp_text(draw, (cx - dw // 2, plot_y1 + 6), d_lbl, font_axis, fill=0)

    # Smooth connecting trendline across the 7 daily markers
    draw_aa_polyline(draw, [(px, py) for px, py, _ in pts_xy], fill=0, width=2)

    # Floating markers: solid black circle when >= 3.2 rated datum, hollow circle with black ring when < 3.2
    for px, py, e_val in pts_xy:
        if e_val >= rated_eff:
            draw_aa_circle(draw, px, py, r=5, fill=0)
        else:
            draw_aa_circle(draw, px, py, r=5, fill=0, outline=255)

    # Horizontal divider above Bottom Efficiency & Capacity Summary Box
    div_y = y + 206
    draw.line([(x + 12, div_y), (x + w - 12, div_y)], fill=0, width=1)

    # Bottom Section: Real-World Efficiency & HV Pack Capacity Retention
    draw_crisp_text(draw, (x + 14, div_y + 10), "AVG EFFICIENCY", font_lbl, fill=60)
    draw_crisp_text(draw, (x + 14, div_y + 34), "3.18 MI/kWh", font_hero, fill=0)
    draw_crisp_text(draw, (x + 14, div_y + 64), "USED: 161 kWh (14D)", font_lbl, fill=0)

    # Vertical split for Pack Health on bottom right
    mid_vx = x + 198
    draw.line([(mid_vx, div_y + 8), (mid_vx, y + h - 10)], fill=140, width=1)
    draw_crisp_text(draw, (mid_vx + 14, div_y + 10), "PACK HEALTH", font_lbl, fill=60)
    draw_crisp_text(draw, (mid_vx + 14, div_y + 34), "90.9 kWh", font_hero, fill=0)
    draw_crisp_text(draw, (mid_vx + 14, div_y + 64), "100% RATED CAP", font_lbl, fill=0)


def render_panel_05_tire_pressure(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 05 // TIRE PRESSURE
    Visualizes 14-day min/max/current PSI range bars for FL, FR, RL, RR
    relative to the 38 PSI target datum and 32 PSI warning line.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "05 // TIRE PRESSURE",
        "14D PSI",
    )

    font_lbl = get_architect_font(17, bold=True)
    font_val = get_architect_font(19, bold=True)

    # Scale: 30 PSI to 44 PSI
    scale_x0 = x + 64
    scale_x1 = x + w - 76
    scale_w = scale_x1 - scale_x0
    min_psi, max_psi = 30.0, 44.0

    def psi_to_x(psi: float) -> int:
        return int(round(scale_x0 + ((psi - min_psi) / (max_psi - min_psi)) * scale_w))

    # Scale header labels (32 WARN, 38 NOMINAL)
    warn_x = psi_to_x(32.0)
    nom_x = psi_to_x(38.0)
    draw_crisp_text(draw, (warn_x - 10, y + 39), "32", font_lbl, fill=80)
    draw_crisp_text(draw, (nom_x - 10, y + 39), "38 PSI", font_lbl, fill=0)
    draw.line([(x + 10, y + 59), (x + w - 10, y + 59)], fill=160, width=1)

    tires = [
        ("FL", 36.4, 39.2, 37.0),
        ("FR", 36.2, 39.0, 37.0),
        ("RL", 36.8, 39.8, 37.0),
        ("RR", 36.8, 39.8, 37.0),
    ]

    track_top = y + 65
    row_h = 25

    # Vertical dashed datum lines at 32 PSI (low warning) and 38 PSI (nominal target)
    track_bot = track_top + len(tires) * row_h - 4
    draw_dashed_line(draw, (warn_x, track_top), (warn_x, track_bot), dash_len=4, gap_len=4, fill=120, width=1)
    draw_dashed_line(draw, (nom_x, track_top), (nom_x, track_bot), dash_len=5, gap_len=3, fill=0, width=1)

    for idx, (corner, p_min, p_max, p_now) in enumerate(tires):
        ry = track_top + idx * row_h
        cy = ry + 10

        # Corner label on left
        draw_crisp_text(draw, (x + 14, ry + 1), corner, font_val, fill=0)

        # Full track guide line
        draw.line([(scale_x0, cy), (scale_x1, cy)], fill=200, width=2)

        # 14-day min..max range bar (medium gray bar with black end-caps)
        rx0 = psi_to_x(p_min)
        rx1 = psi_to_x(p_max)
        draw.rectangle([rx0, cy - 4, rx1, cy + 4], fill=165, outline=0, width=1)
        draw.line([(rx0, cy - 6), (rx0, cy + 6)], fill=0, width=2)
        draw.line([(rx1, cy - 6), (rx1, cy + 6)], fill=0, width=2)

        # Current PSI marker dot (solid black circle)
        nx = psi_to_x(p_now)
        draw_aa_circle(draw, nx, cy, r=4, fill=0)

        # Current PSI numeric readout on right
        val_s = f"{p_now:.1f}"
        vb = font_val.getbbox(val_s)
        vw = vb[2] - vb[0]
        draw_crisp_text(draw, (x + w - 14 - vw, ry + 1), val_s, font_val, fill=0)


def render_panel_06_inkplate_status(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int) -> None:
    """
    PANEL 06 // INKPLATE STATUS
    Displays current exact % charge, estimated days to recharge, and daily battery drain.
    """
    draw_panel_box(
        draw,
        x,
        y,
        w,
        h,
        "06 // INKPLATE STATUS",
        "",
    )

    font_hero = get_architect_font(26, bold=True)
    font_mid = get_architect_font(20, bold=True)
    font_sub = get_architect_font(18, bold=True)

    # Middle Hero Row: Current Exact % Charge | Days to Recharge
    draw_crisp_text(draw, (x + 14, y + 44), "81%", font_hero, fill=0)
    div_x = x + 84
    draw.line([(div_x, y + 42), (div_x, y + 66)], fill=0, width=2)
    draw_crisp_text(draw, (div_x + 12, y + 47), "56 DAYS TO CHARGE", font_mid, fill=0)

    # Bottom Subtitle Row: Daily Drain
    draw_crisp_text(draw, (x + 14, y + 82), "DAILY DRAIN: -1.4% / DAY", font_sub, fill=0)


def build_base_page2() -> Image.Image:
    """
    Renders the static 1200x820 Page 2 template (base_page2.png) with eink_panel_mode=True.
    Includes all static frames, Compass logo, headers, 6 panel boxes with corner cross-ticks,
    static axes/gridlines/labels, and footer battery outline, leaving dynamic data areas blank
    for the ESP32 to render from /telemetry_log.jsonl.
    """
    prev_mode = CONFIG.get("eink_panel_mode", False)
    CONFIG["eink_panel_mode"] = True
    try:
        w = CONFIG["canvas_width"]
        h = CONFIG["canvas_height"]
        colors = CONFIG["colors"]
        layout = CONFIG["layout"]
        font_cfg = CONFIG.get("fonts", {})

        canvas = Image.new("L", (w, h), colors["white"])
        draw = ImageDraw.Draw(canvas)

        # 1. Outer Blueprint Frame & Crosshairs
        draw_blueprint_frame(draw, colors, layout["blueprint_frame"])

        # 2. Static Header (Compass Logo, Title, Subtitle, Divider)
        hdr_cfg = layout["header"]
        font_title = get_architect_font(font_cfg.get("title", 34), bold=True)
        font_sub = get_architect_font(font_cfg.get("subtitle", 19), bold=True)
        draw_rivian_compass_logo(
            draw,
            hdr_cfg.get("logo_x", 36),
            hdr_cfg.get("logo_y", 26),
            size=hdr_cfg.get("logo_size", 74),
            fill=0,
        )
        draw_crisp_text(draw, (hdr_cfg["title_x"], hdr_cfg["title_y"]), "RIVIAN R2", font_title, fill=0)
        draw_crisp_text(
            draw,
            (hdr_cfg["title_x"], hdr_cfg["subtitle_y"]),
            "TELEMETRY HISTORY & LOG ANALYTICS",
            font_sub,
            fill=0,
        )
        draw.line([(hdr_cfg["logo_x"], hdr_cfg["divider_y"]), (hdr_cfg["meta_right_x"], hdr_cfg["divider_y"])], fill=0, width=2)

        font_axis = get_architect_font(17, bold=True)
        font_val19 = get_architect_font(19, bold=True)

        # 3. Panel 01 Static Box & Y-Axis (X=36, Y=120, W=588, H=294)
        x1, y1, w1, h1 = 36, 120, 588, 294
        draw_panel_box(draw, x1, y1, w1, h1, "01 // STATE OF CHARGE & RANGE TIMELINE", "7-DAY LOG  •  30M INTERVAL")
        draw.line([(x1 + 10, y1 + 66), (x1 + w1 - 10, y1 + 66)], fill=160, width=1)
        p1_x0, p1_x1, p1_y0, p1_y1 = x1 + 54, x1 + w1 - 58, y1 + 78, y1 + h1 - 34
        p1_h = p1_y1 - p1_y0
        for pct_tick in (100, 75, 50, 25, 0):
            gy = int(round(p1_y1 - (pct_tick / 100.0) * p1_h))
            if pct_tick in (0, 100):
                draw.line([(p1_x0, gy), (p1_x1, gy)], fill=0, width=1)
            else:
                draw_dashed_line(draw, (p1_x0, gy), (p1_x1, gy), dash_len=4, gap_len=4, fill=150, width=1)
            lbl_l = f"{pct_tick}%"
            bl = font_axis.getbbox(lbl_l)
            draw_crisp_text(draw, (p1_x0 - 6 - (bl[2] - bl[0]), gy - 8), lbl_l, font_axis, fill=0)
            if pct_tick in (100, 50, 0):
                mi_val = int(round(338 * (pct_tick / 100.0)))
                draw_crisp_text(draw, (p1_x1 + 6, gy - 8), f"{mi_val}m", font_axis, fill=50)
        draw.line([(p1_x0, p1_y0), (p1_x0, p1_y1)], fill=0, width=2)
        draw.line([(p1_x1, p1_y0), (p1_x1, p1_y1)], fill=0, width=1)

        # 4. Panel 02 Static Box & Y-Axis (X=638, Y=120, W=526, H=294)
        x2, y2, w2, h2 = 638, 120, 526, 294
        draw_panel_box(draw, x2, y2, w2, h2, "02 // DAILY DISTANCE DRIVEN", "14-DAY HISTOGRAM")
        draw.line([(x2 + 10, y2 + 66), (x2 + w2 - 10, y2 + 66)], fill=160, width=1)
        p2_x0, p2_x1, p2_y0, p2_y1 = x2 + 48, x2 + w2 - 18, y2 + 80, y2 + h2 - 34
        p2_h = p2_y1 - p2_y0
        for y_tick in (100, 75, 50, 25, 0):
            gy = int(round(p2_y1 - (y_tick / 100.0) * p2_h))
            if y_tick == 0:
                draw.line([(p2_x0, gy), (p2_x1, gy)], fill=0, width=2)
            else:
                draw_dashed_line(draw, (p2_x0, gy), (p2_x1, gy), dash_len=4, gap_len=4, fill=160, width=1)
            if y_tick in (100, 50, 0):
                lbl = f"{y_tick}"
                b = font_axis.getbbox(lbl)
                draw_crisp_text(draw, (p2_x0 - 8 - (b[2] - b[0]), gy - 8), lbl, font_axis, fill=0)
        draw.line([(p2_x0, p2_y0), (p2_x0, p2_y1)], fill=0, width=2)

        # 5. Panel 03 Static Box, Legend Keys, & Y-Axis (X=36, Y=428, W=368, H=296)
        x3, y3, w3, h3 = 36, 428, 368, 296
        draw_panel_box(draw, x3, y3, w3, h3, "03 // 3-ZONE THERMAL", "7-DAY (°F)")
        leg_y = y3 + 42
        draw.line([(x3 + 14, leg_y + 8), (x3 + 34, leg_y + 8)], fill=0, width=3)
        draw_dashed_line(draw, (x3 + 132, leg_y + 8), (x3 + 154, leg_y + 8), dash_len=5, gap_len=3, fill=0, width=2)
        draw.line([(x3 + 258, leg_y + 8), (x3 + 278, leg_y + 8)], fill=120, width=2)
        draw.line([(x3 + 10, y3 + 66), (x3 + w3 - 10, y3 + 66)], fill=160, width=1)
        p3_x0, p3_x1, p3_y0, p3_y1 = x3 + 46, x3 + w3 - 26, y3 + 80, y3 + h3 - 34
        p3_h = p3_y1 - p3_y0
        for t_tick in (90, 65, 40):
            gy = int(round(p3_y1 - ((t_tick - 40.0) / 50.0) * p3_h))
            if t_tick == 40:
                draw.line([(p3_x0, gy), (p3_x1, gy)], fill=0, width=2)
            else:
                draw_dashed_line(draw, (p3_x0, gy), (p3_x1, gy), dash_len=4, gap_len=4, fill=165, width=1)
            lbl = f"{t_tick}°"
            b = font_axis.getbbox(lbl)
            draw_crisp_text(draw, (p3_x0 - 6 - (b[2] - b[0]), gy - 8), lbl, font_axis, fill=0)
        draw.line([(p3_x0, p3_y0), (p3_x0, p3_y1)], fill=0, width=2)

        # 6. Panel 04 Static Box, Y-Axis (4.5 / 3.0 / 1.5), & Bottom Headers (X=416, Y=428, W=368, H=296)
        x4, y4, w4, h4 = 416, 428, 368, 296
        draw_panel_box(draw, x4, y4, w4, h4, "04 // EFFICIENCY & CAPACITY", "7D MI/kWh")
        p4_x0, p4_x1, p4_y0, p4_y1 = x4 + 46, x4 + w4 - 18, y4 + 48, y4 + 174
        p4_h = p4_y1 - p4_y0
        for e_tick in (4.5, 3.0, 1.5):
            gy = int(round(p4_y1 - ((e_tick - 1.5) / 3.0) * p4_h))
            if e_tick == 1.5:
                draw.line([(p4_x0, gy), (p4_x1, gy)], fill=0, width=2)
            elif e_tick == 3.0:
                draw_dashed_line(draw, (p4_x0, gy), (p4_x1, gy), dash_len=6, gap_len=4, fill=0, width=2)
            else:
                draw_dashed_line(draw, (p4_x0, gy), (p4_x1, gy), dash_len=4, gap_len=4, fill=165, width=1)
            lbl = f"{e_tick:.1f}"
            b = font_axis.getbbox(lbl)
            draw_crisp_text(draw, (p4_x0 - 6 - (b[2] - b[0]), gy - 8), lbl, font_axis, fill=0)
        draw.line([(p4_x0, p4_y0), (p4_x0, p4_y1)], fill=0, width=2)
        div4_y = y4 + 206
        draw.line([(x4 + 12, div4_y), (x4 + w4 - 12, div4_y)], fill=0, width=1)
        draw_crisp_text(draw, (x4 + 14, div4_y + 10), "AVG EFFICIENCY", font_axis, fill=60)
        mid4_vx = x4 + 198
        draw.line([(mid4_vx, div4_y + 8), (mid4_vx, y4 + h4 - 10)], fill=140, width=1)
        draw_crisp_text(draw, (mid4_vx + 14, div4_y + 10), "PACK HEALTH", font_axis, fill=60)
        draw_crisp_text(draw, (mid4_vx + 14, div4_y + 64), "100% RATED CAP", font_axis, fill=0)

        # 7. Panel 05 Static Box, 32/38 PSI Datum Lines, & FL/FR/RL/RR Track Guides (X=796, Y=428, W=368, H=170)
        x5, y5, w5, h5 = 796, 428, 368, 170
        draw_panel_box(draw, x5, y5, w5, h5, "05 // TIRE PRESSURE", "14D PSI")
        sc_x0, sc_x1 = x5 + 64, x5 + w5 - 76
        sc_w = sc_x1 - sc_x0
        warn_x = int(round(sc_x0 + ((32.0 - 30.0) / 14.0) * sc_w))
        nom_x = int(round(sc_x0 + ((38.0 - 30.0) / 14.0) * sc_w))
        draw_crisp_text(draw, (warn_x - 10, y5 + 39), "32", font_axis, fill=80)
        draw_crisp_text(draw, (nom_x - 10, y5 + 39), "38 PSI", font_axis, fill=0)
        draw.line([(x5 + 10, y5 + 59), (x5 + w5 - 10, y5 + 59)], fill=160, width=1)
        t_top = y5 + 65
        r_h = 25
        t_bot = t_top + 4 * r_h - 4
        draw_dashed_line(draw, (warn_x, t_top), (warn_x, t_bot), dash_len=4, gap_len=4, fill=120, width=1)
        draw_dashed_line(draw, (nom_x, t_top), (nom_x, t_bot), dash_len=5, gap_len=3, fill=0, width=1)
        for idx, corner in enumerate(("FL", "FR", "RL", "RR")):
            ry = t_top + idx * r_h
            cy = ry + 10
            draw_crisp_text(draw, (x5 + 14, ry + 1), corner, font_val19, fill=0)
            draw.line([(sc_x0, cy), (sc_x1, cy)], fill=200, width=2)

        # 8. Panel 06 Static Box & Vertical Divider (X=796, Y=612, W=368, H=112)
        x6, y6, w6, h6 = 796, 612, 368, 112
        draw_panel_box(draw, x6, y6, w6, h6, "06 // INKPLATE STATUS", "")
        div6_x = x6 + 84
        draw.line([(div6_x, y6 + 42), (div6_x, y6 + 66)], fill=0, width=2)

        # 9. Bottom Ground Line & Empty Inkplate Battery Icon Outline
        # (Left edition + Born On footer is stamped dynamically at runtime from config.json)
        ground_y = 740
        draw.line([(35, ground_y), (1165, ground_y)], fill=0, width=2)
        font_datum = get_architect_font(font_cfg.get("ground_datum", 19), bold=True)
        draw_inkplate_battery_status(draw, 1164, ground_y + 8, pct=0, font=font_datum, colors=colors, draw_fill=False)

        return canvas
    finally:
        CONFIG["eink_panel_mode"] = prev_mode


def main() -> None:
    w = CONFIG["canvas_width"]
    h = CONFIG["canvas_height"]
    colors = CONFIG["colors"]
    layout = CONFIG["layout"]

    canvas = Image.new("L", (w, h), colors["white"])
    draw = ImageDraw.Draw(canvas)

    # 1. Outer Architectural Blueprint Frame & Corner Crosshairs
    draw_blueprint_frame(draw, colors, layout["blueprint_frame"])

    # 2. Top Header Block (Matching Page 1 geometry with Page 2 Subtitle & Sheet Metadata)
    hdr_cfg = layout["header"]
    font_cfg = CONFIG.get("fonts", {})
    font_title = get_architect_font(font_cfg.get("title", 34), bold=True)
    font_sub = get_architect_font(font_cfg.get("subtitle", 19), bold=True)
    font_meta = get_architect_font(font_cfg.get("meta_drawing", 20), bold=True)
    font_meta_sub = get_architect_font(font_cfg.get("meta_status", 19), bold=True)

    draw_rivian_compass_logo(
        draw,
        hdr_cfg.get("logo_x", 36),
        hdr_cfg.get("logo_y", 26),
        size=hdr_cfg.get("logo_size", 74),
        fill=0,
    )

    draw_crisp_text(draw, (hdr_cfg["title_x"], hdr_cfg["title_y"]), "RIVIAN R2", font_title, fill=0)
    draw_crisp_text(
        draw,
        (hdr_cfg["title_x"], hdr_cfg["subtitle_y"]),
        "TELEMETRY HISTORY & LOG ANALYTICS",
        font_sub,
        fill=0,
    )

    # Right-aligned Title Block Metadata
    meta_line1 = "DWG NO. R2-EPD-01  /  REV 1.0  •  PAGE 02/02"
    b1 = font_meta.getbbox(meta_line1)
    draw_crisp_text(draw, (hdr_cfg["meta_right_x"] - (b1[2] - b1[0]), hdr_cfg["meta_y1"]), meta_line1, font_meta, fill=0)

    meta_line2 = "LOGGED: 14 DAYS (448 SAMPLES)  •  SYNC: TODAY  •  3:18 PM"
    b2 = font_meta_sub.getbbox(meta_line2)
    draw_crisp_text(
        draw,
        (hdr_cfg["meta_right_x"] - (b2[2] - b2[0]), hdr_cfg["meta_y2"]),
        meta_line2,
        font_meta_sub,
        fill=0,
    )

    draw.line([(hdr_cfg["logo_x"], hdr_cfg["divider_y"]), (hdr_cfg["meta_right_x"], hdr_cfg["divider_y"])], fill=0, width=2)

    # 3. Render the 5 Telemetry History Panels (Y=120..724)
    # Row 1 (Y=120, H=294):
    # - Panel 01: SOC & Range Timeline (X=36, W=588)
    # - Panel 02: Daily Distance Driven 14-Day Histogram (X=638, W=526)
    render_panel_01_soc_timeline(draw, x=36, y=120, w=588, h=294)
    render_panel_02_daily_mileage(draw, x=638, y=120, w=526, h=294)

    # Row 2 (Y=428..724):
    # - Panel 03: 3-Zone Thermal Profile (X=36, Y=428, W=368, H=296)
    # - Panel 04: Efficiency & Capacity (X=416, Y=428, W=368, H=296)
    # - Panel 05: Tire Pressure (X=796, Y=428, W=368, H=170)
    # - Panel 06: Inkplate Status (X=796, Y=612, W=368, H=112)
    render_panel_03_thermal_profile(draw, x=36, y=428, w=368, h=296)
    render_panel_04_charger_and_efficiency(draw, x=416, y=428, w=368, h=296)
    render_panel_05_tire_pressure(draw, x=796, y=428, w=368, h=170)
    render_panel_06_inkplate_status(draw, x=796, y=612, w=368, h=112)

    # 4. Bottom Ground Line (Y=740), Edition Footer, and Inkplate 10 Battery Status
    ground_y = 740
    draw.line([(35, ground_y), (1165, ground_y)], fill=0, width=2)

    font_datum = get_architect_font(font_cfg.get("ground_datum", 19), bold=True)
    draw_inkplate_battery_status(draw, 1164, ground_y + 8, pct=81, font=font_datum, colors=colors, draw_fill=True)

    font_footer = get_architect_font(font_cfg.get("ground_footer", 19), bold=True)
    footer_text = "RIVIAN R2  //  BORN ON: 2026-01-01"
    draw_crisp_text(draw, (38, ground_y + 9), footer_text, font_footer, fill=0)

    os.makedirs(os.path.dirname(os.path.abspath(OUTPUT_FILE)), exist_ok=True)
    canvas.save(OUTPUT_FILE)
    print(f"Saved Page 2 mockup to {OUTPUT_FILE} ({canvas.size[0]}x{canvas.size[1]})")


if __name__ == "__main__":
    main()
