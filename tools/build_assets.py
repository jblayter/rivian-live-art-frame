#!/usr/bin/env python3
"""
Builds all device assets, pre-rasterized 3-bit grayscale display fonts,
MicroPython runtime modules, and config.json for the Inkplate 10 (1200x820).
"""

import json
import os
import shutil
import sys
from PIL import Image, ImageDraw

import vehicles
from generate_mockup import (
    CONFIG,
    TELEMETRY,
    VEHICLE,
    draw_aa_polyline,
    draw_blueprint_frame,
    draw_crisp_text,
    draw_inkplate_battery_status,
    draw_rivian_compass_logo,
    draw_wireframe,
    get_architect_font,
)

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SD_STAGING_DIR = os.path.join(WORKSPACE_DIR, "device")
SD_ASSETS_DIR = os.path.join(SD_STAGING_DIR, "assets")   # shared files (fonts) + R2 base blueprints
VEHICLE_ASSETS_DIR = vehicles.assets_dir(VEHICLE)         # this vehicle's base blueprints
SD_VOLUME_PATH = "/Volumes/INKPLATE"


def build_base_blueprint() -> Image.Image:
    """
    Renders the 1200x820 static base blueprint with all static frames, wireframe,
    leader lines, card outlines, card headers, and tire boxes/labels, leaving
    all dynamic values (including header metadata and edition/born-on footer)
    blank white for the ESP32 to stamp at runtime from config.json.
    """
    CONFIG["eink_panel_mode"] = True
    w = CONFIG["canvas_width"]   # 1200
    h = CONFIG["canvas_height"]  # 820
    colors = CONFIG["colors"]
    layout = CONFIG["layout"]
    dots = CONFIG["terminal_dots"]
    wf_path = os.path.join(WORKSPACE_DIR, CONFIG["wireframe_file"])
    font_cfg = CONFIG.get("fonts", {})

    canvas = Image.new("L", (w, h), colors["white"])
    draw = ImageDraw.Draw(canvas)

    # 1. Outer blueprint border & corner crosshairs
    draw_blueprint_frame(draw, colors, layout["blueprint_frame"])

    # 2. Vehicle Wireframe
    draw_wireframe(canvas, wf_path, colors, layout["wireframe"])

    # 3. Datum Ground Line & Right Static Inkplate 10 Battery Outline
    # (Left edition + Born On footer is stamped dynamically at runtime from config.json)
    ground_y = layout["wireframe"].get("ground_y", 740)
    draw.line([(35, ground_y), (1165, ground_y)], fill=colors["mid_gray"], width=2)
    font_datum = get_architect_font(font_cfg.get("ground_datum", 19), bold=True)
    draw_inkplate_battery_status(draw, 1164, ground_y + 8, pct=0, font=font_datum, colors=colors, draw_fill=False)

    # 4. Static Header Elements (Rivian Compass Logo + Left Title/Subtitle + Divider)
    hdr_cfg = layout["header"]
    font_title = get_architect_font(font_cfg.get("title", 34), bold=True)
    font_sub = get_architect_font(font_cfg.get("subtitle", 19), bold=True)

    logo_x = hdr_cfg.get("logo_x", 36)
    logo_y = hdr_cfg.get("logo_y", 26)
    logo_sz = hdr_cfg.get("logo_size", 74)
    draw_rivian_compass_logo(draw, logo_x, logo_y, size=logo_sz, fill=colors["black"])

    draw_crisp_text(draw, (hdr_cfg["title_x"], hdr_cfg["title_y"]), TELEMETRY["vehicleName"], font_title, fill=colors["black"])
    draw_crisp_text(draw, (hdr_cfg["title_x"], hdr_cfg["subtitle_y"]), TELEMETRY["blueprintSubtitle"], font_sub, fill=colors["dark_gray"])

    # Note: Both right-aligned header metadata lines ("DWG NO. R2-EPD-01 / REV ..." and
    # "STATUS: ONLINE • SYNC: ...") are stamped dynamically at runtime from config.json!
    draw.line([(logo_x, hdr_cfg["divider_y"]), (hdr_cfg["meta_right_x"], hdr_cfg["divider_y"])], fill=colors["mid_gray"], width=2)

    # 5. Static Metric Card Outlines, Anti-Aliased Leader Lines, Corner Ticks, and Category Headers
    m_cfg = layout["metrics_grid"]
    start_x = m_cfg["start_x"]
    cy = m_cfg["y"]
    cw = m_cfg["card_width"]
    ch = m_cfg["card_height"]
    gap = m_cfg["card_gap"]
    bw = m_cfg["border_width"]
    card_bottom_y = cy + ch
    dot_r = m_cfg.get("terminal_dot_radius", 4)
    lw = m_cfg.get("card_leader_width", 2)
    shoulder = m_cfg.get("shoulder_length", 14)

    card_defs = [
        (None,         "01 // BATTERY LEVEL"),
        ("odometer",   "02 // ODOMETER"),
        ("climate",    "03 // CLIMATE"),
        ("battery12v", "04 // BATTERY STATE"),
        ("charger",    "05 // CHARGER"),
    ]
    # Front charge port (e.g. R1T): swap the first and last cards and renumber them
    battery_idx = 0
    if CONFIG.get("charger_card_first", False):
        battery_idx = 4
        card_defs[0], card_defs[4] = ("charger", "01 // CHARGER"), (None, "05 // BATTERY LEVEL")
    font_sec = get_architect_font(font_cfg.get("card_header", 18), bold=True)

    # Anti-aliased leader lines first
    for i, (dot_key, _) in enumerate(card_defs):
        if dot_key and dot_key in dots:
            card_x = start_x + i * (cw + gap)
            center_x = card_x + (cw // 2)
            tx, ty = dots[dot_key]
            draw_aa_polyline(draw, [(center_x, card_bottom_y), (center_x, card_bottom_y + shoulder), (tx, ty)], fill=colors["black"], width=lw)
            draw.ellipse([tx - dot_r, ty - dot_r, tx + dot_r, ty + dot_r], fill=colors["black"])

    # Card containers & static headers
    for i, (_, label) in enumerate(card_defs):
        cx = start_x + i * (cw + gap)
        draw.rectangle([cx, cy, cx + cw, cy + ch], outline=colors["mid_gray"], fill=colors["white"], width=bw)
        if m_cfg.get("show_corner_ticks", True):
            t_len = 5
            draw.line([(cx - t_len, cy), (cx + t_len, cy)], fill=colors["black"], width=2)
            draw.line([(cx, cy - t_len), (cx, cy + t_len)], fill=colors["black"], width=2)
            draw.line([(cx + cw - t_len, cy), (cx + cw + t_len, cy)], fill=colors["black"], width=2)
            draw.line([(cx + cw, cy - t_len), (cx + cw, cy + t_len)], fill=colors["black"], width=2)
        draw.line([(cx + 8, cy + 34), (cx + cw - 8, cy + 34)], fill=colors["light_gray"], width=2)
        draw_crisp_text(draw, (cx + 10, cy + 8), label, font_sec, fill=colors["dark_gray"])

        # Static battery bar outline on the Battery Level card
        if i == battery_idx:
            bar_x = cx + 82
            bar_y = cy + 46
            bar_w = cw - 102
            bar_h = 20
            draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], outline=colors["black"], fill=colors["white"], width=2)
            cap_w, cap_h = 5, 10
            cap_y = bar_y + (bar_h - cap_h) // 2
            draw.rectangle([bar_x + bar_w + 1, cap_y, bar_x + bar_w + cap_w, cap_y + cap_h], outline=colors["black"], fill=colors["black"], width=1)

        # Static vertical divider tick on Card 4 (04 // BATTERY STATE)
        elif i == 3:
            div_x = cx + 128
            draw.line([(div_x, cy + 44), (div_x, cy + 68)], fill=colors["mid_gray"], width=2)

    # 6. Static Tire Pressure Boxes, Anti-Aliased Leader Lines, and Labels (leaving PSI values blank)
    tp_cfg = layout["tire_pressures"]
    tbw = tp_cfg["badge_width"]
    tbh = tp_cfg["badge_height"]
    tdot_r = tp_cfg.get("terminal_dot_radius", 4)
    tlw = tp_cfg.get("leader_line_width", 2)
    font_tlbl = get_architect_font(font_cfg.get("tire_label", 18), bold=True)

    fl_cfg = tp_cfg["front_left"]
    fl_target = dots.get("tire_fl", (300, 641))
    draw_aa_polyline(draw, [fl_cfg["leader_start"], fl_cfg["leader_mid"], fl_target], fill=colors["black"], width=tlw)
    draw.ellipse([fl_target[0] - tdot_r, fl_target[1] - tdot_r, fl_target[0] + tdot_r, fl_target[1] + tdot_r], fill=colors["black"])

    fr_cfg = tp_cfg["front_right"]
    fr_target = dots.get("tire_fr", (305, 681))
    draw_aa_polyline(draw, [fr_cfg["leader_start"], fr_cfg["leader_mid1"], fr_cfg["leader_mid2"], fr_target], fill=colors["black"], width=tlw)
    draw.ellipse([fr_target[0] - tdot_r, fr_target[1] - tdot_r, fr_target[0] + tdot_r, fr_target[1] + tdot_r], fill=colors["black"])

    rl_cfg = tp_cfg["rear_left"]
    rl_target = dots.get("tire_rl", (875, 681))
    draw_aa_polyline(draw, [rl_cfg["leader_start"], rl_cfg["leader_mid1"], rl_cfg["leader_mid2"], rl_target], fill=colors["black"], width=tlw)
    draw.ellipse([rl_target[0] - tdot_r, rl_target[1] - tdot_r, rl_target[0] + tdot_r, rl_target[1] + tdot_r], fill=colors["black"])

    rr_cfg = tp_cfg["rear_right"]
    rr_target = dots.get("tire_rr", (830, 641))
    draw_aa_polyline(draw, [rr_cfg["leader_start"], rr_cfg["leader_mid"], rr_target], fill=colors["black"], width=tlw)
    draw.ellipse([rr_target[0] - tdot_r, rr_target[1] - tdot_r, rr_target[0] + tdot_r, rr_target[1] + tdot_r], fill=colors["black"])

    for pos in (fl_cfg, fr_cfg, rl_cfg, rr_cfg):
        bx, by = pos["x"], pos["y"]
        draw.rectangle([bx, by, bx + tbw, by + tbh], fill=colors["white"], outline=colors["black"], width=2)
        draw.line([(bx + 6, by + 25), (bx + tbw - 6, by + 25)], fill=colors["light_gray"], width=2)
        draw_crisp_text(draw, (bx + 10, by + 4), pos["label"], font_tlbl, fill=colors["dark_gray"])

    mid_x = (fr_cfg["x"] + tbw + rl_cfg["x"]) // 2
    by = fl_cfg["y"]
    draw.line([(mid_x, by + 4), (mid_x, by + tbh - 4)], fill=colors["mid_gray"], width=2)

    return canvas


def save_packed_3bit(img: Image.Image, out_path: str) -> None:
    """
    Saves the 1200x820 grayscale image as a packed 4-bit-per-pixel (2 pixels per byte)
    file with values 0..7 matching Inkplate 10's GS4 _framebuf byte layout:
    even x in low nibble (bits 0..3), odd x in high nibble (bits 4..7), 0=black, 7=white.
    Total size: 1200 * 820 // 2 = 492,000 bytes.
    """
    raw = img.tobytes()
    packed = bytearray(len(raw) // 2)
    for idx in range(0, len(raw), 2):
        g0 = raw[idx] >> 5      # even x: 0..255 -> 0..7 (low nibble)
        g1 = raw[idx + 1] >> 5  # odd x:  0..255 -> 0..7 (high nibble)
        packed[idx >> 1] = (g1 << 4) | g0
    with open(out_path, "wb") as f:
        f.write(packed)


def build_micropython_font_module(out_py_path: str) -> None:
    """
    Pre-rasterizes the active architectural font at sizes 17, 18, 19, 20, 22, and 26 using 4x-supersampled
    anti-aliasing and an e-ink contrast curve. Smaller sizes (<= 20px) use a subtle horizontal-only
    subpixel weight so lowercase counters ('e', 'a') stay open and distinct.
    """
    charset = [chr(c) for c in range(32, 127)] + ["°", "•"]
    sizes = [17, 18, 19, 20, 22, 26]
    fonts_data = {}
    scale = 4

    for sz in sizes:
        core_thresh = 138 if sz <= 20 else 128

        def _to_ink7(a: int) -> int:
            if a >= core_thresh:
                return 7  # Solid pure black (0) core
            if a >= 38:
                return 3 + int((a - 38) * 3 / (core_thresh - 38))  # Smooth dark AA edge
            return 0  # Transparent / white

        font = get_architect_font(sz, bold=True)
        font_hi = get_architect_font(getattr(font, "size", sz) * scale, bold=True, _raw_size=True)
        glyphs = {}
        for ch in charset:
            bbox = font_hi.getbbox(ch)
            advance = int(round(font.getlength(ch))) + (1 if (sz > 20 and ch != " ") else 0)
            if bbox is None:
                glyphs[ch] = (advance, 0, 0, 0, 0, b"")
                continue
            x0_hi, y0_hi, x1_hi, y1_hi = bbox
            pad = 6
            gw = max(1, (x1_hi - x0_hi + pad * 2 + scale - 1) // scale)
            gh = max(1, (y1_hi - y0_hi + pad * 2 + scale - 1) // scale)
            im_hi = Image.new("L", (gw * scale, gh * scale), 0)
            d_hi = ImageDraw.Draw(im_hi)
            if sz <= 20:
                for dx in (0, 1, 2):
                    for dy in (0, 1):
                        d_hi.text((-x0_hi + pad + dx, -y0_hi + pad + dy), ch, fill=255, font=font_hi)
            else:
                for dx in (-1, 0, 1):
                    for dy in (0, 1):
                        d_hi.text((-x0_hi + pad + dx, -y0_hi + pad + dy), ch, fill=255, font=font_hi)
            im = im_hi.resize((gw, gh), resample=Image.Resampling.LANCZOS)
            x0 = (x0_hi - pad) // scale
            y0 = (y0_hi - pad) // scale
            px = im.tobytes()
            if len(px) % 2 == 1:
                px += b"\x00"
            packed = bytes((_to_ink7(px[k]) << 4) | _to_ink7(px[k + 1]) for k in range(0, len(px), 2))
            glyphs[ch] = (advance, x0, y0, gw, gh, packed)
        fonts_data[sz] = glyphs

    lines = [
        '"""Pre-rasterized 3-bit anti-aliased architectural display fonts (17, 18, 19, 20, 22, 26px) for MicroPython."""',
        "",
        "FONTS = {",
    ]
    for sz, glyphs in fonts_data.items():
        lines.append(f"    {sz}: {{")
        for ch, (adv, x0, y0, gw, gh, packed) in glyphs.items():
            lines.append(f"        {ch!r}: ({adv}, {x0}, {y0}, {gw}, {gh}, {packed!r}),")
        lines.append("    },")
    lines.append("}")
    lines.append(
        """

def measure_text(text: str, size: int = 18) -> int:
    table = FONTS.get(size, FONTS[18])
    total = 0
    for ch in text:
        g = table.get(ch) or table.get(" ")
        if g:
            total += g[0]
    return total


def draw_text(display, x: int, y: int, text: str, size: int = 18, ink_level: int = 0) -> int:
    \"\"\"
    Draws anti-aliased 3-bit grayscale text onto an Inkplate 10 display object.
    Uses darkest-ink-wins compositing (0=black, 7=white) so adjacent glyph AA edges never lighten stems.
    \"\"\"
    table = FONTS.get(size, FONTS[18])
    cx = x
    span = 7 - ink_level
    fb = display._framebuf() if hasattr(display, "_framebuf") else None
    set_px = getattr(display, "write_pixel", None) or getattr(display, "draw_pixel", None) or getattr(display, "drawPixel", None)
    for ch in text:
        g = table.get(ch)
        if not g:
            continue
        adv, x0, y0, gw, gh, packed = g
        if gw > 0 and gh > 0 and packed:
            idx = 0
            for row in range(gh):
                py = y + y0 + row
                row_off = py * 600
                for col in range(gw):
                    nibble = (packed[idx >> 1] >> 4) if (idx & 1) == 0 else (packed[idx >> 1] & 0x0F)
                    idx += 1
                    if nibble > 0:
                        c = 7 - ((nibble * span) // 7)
                        px_x = cx + x0 + col
                        if fb is not None and 0 <= px_x < 1200 and 0 <= py < 820:
                            b_idx = row_off + (px_x >> 1)
                            cur = fb[b_idx]
                            if (px_x & 1) == 0:
                                if c < (cur & 0x0F):
                                    fb[b_idx] = (cur & 0xF0) | c
                            else:
                                if c < (cur >> 4):
                                    fb[b_idx] = (cur & 0x0F) | (c << 4)
                        else:
                            set_px(px_x, py, c)
        cx += adv
    return cx - x


def draw_reversed_text(
    display,
    x: int,
    y: int,
    text: str,
    size: int = 18,
    pad_x: int = 5,
    pad_y: int = 3,
    box_w: int = None,
) -> int:
    \"\"\"
    Draws a solid black (0) warning rectangle and renders anti-aliased white (7) text inside it.
    \"\"\"
    table = FONTS.get(size, FONTS[18])
    tw = measure_text(text, size=size)
    min_y0 = 0
    max_y1 = size - 4
    y0s = []
    y1s = []
    for ch in text:
        g = table.get(ch)
        if g and g[4] > 0:
            y0s.append(g[2])
            y1s.append(g[2] + g[4])
    if y0s and y1s:
        min_y0 = min(y0s)
        max_y1 = max(y1s)

    rx0 = max(0, x - pad_x)
    ry0 = max(0, y + min_y0 - pad_y)
    rect_w = box_w if box_w is not None else (tw + pad_x * 2)
    rx1 = min(1200, rx0 + rect_w)
    ry1 = min(820, y + max_y1 + pad_y)

    fb = display._framebuf() if hasattr(display, "_framebuf") else None
    set_px = getattr(display, "write_pixel", None) or getattr(display, "draw_pixel", None) or getattr(display, "drawPixel", None)

    if hasattr(display, "fill_rect"):
        display.fill_rect(rx0, ry0, rx1 - rx0, ry1 - ry0, 0)
    elif fb is not None:
        for py in range(ry0, ry1):
            row_off = py * 600
            for px_x in range(rx0, rx1):
                b_idx = row_off + (px_x >> 1)
                if (px_x & 1) == 0:
                    fb[b_idx] = fb[b_idx] & 0xF0
                else:
                    fb[b_idx] = fb[b_idx] & 0x0F
    else:
        for py in range(ry0, ry1):
            for px_x in range(rx0, rx1):
                set_px(px_x, py, 0)

    cx = x + ((rect_w - pad_x * 2 - tw) // 2 if box_w is not None else 0)
    for ch in text:
        g = table.get(ch)
        if not g:
            continue
        adv, x0, y0, gw, gh, packed = g
        if gw > 0 and gh > 0 and packed:
            idx = 0
            for row in range(gh):
                py = y + y0 + row
                row_off = py * 600
                for col in range(gw):
                    nibble = (packed[idx >> 1] >> 4) if (idx & 1) == 0 else (packed[idx >> 1] & 0x0F)
                    idx += 1
                    if nibble > 0:
                        c = nibble  # 7 = pure white core, 3..6 = smooth AA edge on black
                        px_x = cx + x0 + col
                        if fb is not None and 0 <= px_x < 1200 and 0 <= py < 820:
                            b_idx = row_off + (px_x >> 1)
                            cur = fb[b_idx]
                            if (px_x & 1) == 0:
                                if c > (cur & 0x0F):
                                    fb[b_idx] = (cur & 0xF0) | c
                            else:
                                if c > (cur >> 4):
                                    fb[b_idx] = (cur & 0x0F) | (c << 4)
                        else:
                            set_px(px_x, py, c)
        cx += adv
    return rect_w
"""
    )

    with open(out_py_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def build_sd_config(out_cfg_path: str) -> None:
    """Builds the SD card config.json combining Wi-Fi settings and Rivian session tokens."""
    src_cfg_path = os.path.join(WORKSPACE_DIR, "config.json")
    with open(src_cfg_path, "r", encoding="utf-8") as f:
        src = json.load(f)

    sd_cfg = {
        "wifi_ssid": src.get("wifi_ssid", "YOUR_WIFI_SSID"),
        "wifi_password": src.get("wifi_password", "YOUR_WIFI_PASSWORD"),
        "wifi_bssid": src.get("wifi_bssid", None),
        "wifi_channel": src.get("wifi_channel", None),
        "refresh_interval_minutes": src.get("refresh_interval_minutes", 30),
        "timezone_offset_hours": src.get("timezone_offset_hours", -5),
        "quiet_hours_start": src.get("quiet_hours_start", 23),
        "quiet_hours_end": src.get("quiet_hours_end", 5),
        "low_battery_threshold": src.get("low_battery_threshold", 15),
        "low_tire_psi_threshold": src.get("low_tire_psi_threshold", 32.0),
        "vehicle_id": src.get("vehicle_id", ""),
        "vehicle_name": src.get("vehicle_name", ""),
        "vehicle_model": VEHICLE,
        "footer_edition": src.get("footer_edition") or vehicles.profile(VEHICLE)["name"],
        "drawing_number": src.get("drawing_number") or vehicles.profile(VEHICLE)["drawing_number"],
        "revision": src.get("revision", "REV 1.0"),
        "charger_card_first": bool(CONFIG.get("charger_card_first", False)),
        "active_page": src.get("active_page", 1),
        "actual_general_assembly_date": src.get("actual_general_assembly_date", ""),
        "refresh_token": src.get("refresh_token"),
        "user_session_token": src.get("user_session_token"),
        "access_token": src.get("access_token"),
        "csrf_token": src.get("csrf_token"),
        "app_session_token": src.get("app_session_token"),
        "last_sync_epoch": src.get("last_sync_epoch"),
        "last_sync_time_str": src.get("last_sync_time_str"),
        "last_telemetry": src.get("last_telemetry"),
    }
    with open(out_cfg_path, "w", encoding="utf-8") as f:
        json.dump(sd_cfg, f, indent=4)


def _find_inkplate_port():
    """Cross-platform detection of a connected Inkplate 10 USB-serial port."""
    try:
        from serial.tools import list_ports

        for p in list_ports.comports():
            desc = f"{p.description or ''} {p.manufacturer or ''} {p.device or ''}".lower()
            if any(k in desc for k in ("cp210", "ch340", "ch910", "usbserial", "slab_usbtouart", "ttyusb", "ftdi")):
                return p.device
    except Exception:
        pass
    import glob

    for pattern in ("/dev/cu.usbserial-*", "/dev/cu.SLAB_USBtoUART*", "/dev/ttyUSB*"):
        matches = sorted(glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def _find_mpremote():
    """Find mpremote in the local virtualenv or system PATH (macOS/Linux/Windows)."""
    candidates = [
        os.path.join(WORKSPACE_DIR, ".venv", "bin", "mpremote"),
        os.path.join(WORKSPACE_DIR, ".venv", "Scripts", "mpremote.exe"),
        shutil.which("mpremote"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def main() -> None:
    build_only = "--build-only" in sys.argv[1:]
    os.makedirs(SD_ASSETS_DIR, exist_ok=True)
    os.makedirs(VEHICLE_ASSETS_DIR, exist_ok=True)
    print(f"Vehicle: {TELEMETRY['vehicleName']}  ->  {os.path.relpath(VEHICLE_ASSETS_DIR, WORKSPACE_DIR)}")

    print("[1/5] Generating 1200x820 static base blueprint templates (Page 1 & Page 2)...")
    base_img = build_base_blueprint()
    bmp_path = os.path.join(VEHICLE_ASSETS_DIR, "base_blueprint.bmp")
    png_path = os.path.join(VEHICLE_ASSETS_DIR, "base_blueprint.png")
    raw3_path = os.path.join(VEHICLE_ASSETS_DIR, "base_blueprint.3bit")
    base_img.save(bmp_path, format="BMP")
    base_img.save(png_path, format="PNG")
    save_packed_3bit(base_img, raw3_path)

    from generate_page2_mockup import build_base_page2
    page2_img = build_base_page2()
    page2_png_path = os.path.join(VEHICLE_ASSETS_DIR, "base_page2.png")
    page2_img.save(page2_png_path, format="PNG")
    print(f"      Saved {png_path}, {page2_png_path} ({os.path.getsize(page2_png_path):,} bytes), and {raw3_path}.")

    print("[2/5] Rasterizing architectural font (17, 18, 19, 20, 22, 26px) into MicroPython font module...")
    font_py_path = os.path.join(SD_ASSETS_DIR, "display_fonts.py")
    build_micropython_font_module(font_py_path)
    print(f"      Saved {font_py_path} ({os.path.getsize(font_py_path):,} bytes).")

    if build_only:
        print("[3/5] --build-only: skipped config.json, SD card copy and USB deploy.")
        return

    print("[3/5] Generating SD card config.json...")
    cfg_out_path = os.path.join(SD_STAGING_DIR, "config.json")
    build_sd_config(cfg_out_path)
    print(f"      Saved {cfg_out_path}.")

    if os.path.isdir(SD_VOLUME_PATH):
        print(f"[4/5] Copying files to mounted SD volume '{SD_VOLUME_PATH}'...")
        vol_assets = os.path.join(SD_VOLUME_PATH, "assets")
        os.makedirs(vol_assets, exist_ok=True)
        shutil.copy2(cfg_out_path, os.path.join(SD_VOLUME_PATH, "config.json"))
        for py_name in ("inkplate_app.py", "main.py", "rle_cache.py"):
            py_src = os.path.join(SD_STAGING_DIR, py_name)
            if os.path.exists(py_src):
                shutil.copy2(py_src, os.path.join(SD_VOLUME_PATH, py_name))
        for fname in ("base_blueprint.bmp", "base_blueprint.png", "base_blueprint.3bit", "base_page2.png"):
            shutil.copy2(os.path.join(VEHICLE_ASSETS_DIR, fname), os.path.join(vol_assets, fname))
        shutil.copy2(font_py_path, os.path.join(vol_assets, "display_fonts.py"))
        print(f"      Successfully populated '{SD_VOLUME_PATH}'!")
    else:
        print(f"[4/5] Volume '{SD_VOLUME_PATH}' not mounted; files staged in '{SD_STAGING_DIR}'.")

    # 5. Also deploy directly to Inkplate 10 internal flash if connected over USB
    import subprocess

    port = _find_inkplate_port()
    mpremote_bin = _find_mpremote()
    if port and mpremote_bin:
        print(f"[5/5] Deploying config, app, and assets to Inkplate 10 internal flash on {port}...")
        try:
            import serial, time
            s = serial.Serial(port, 115200, timeout=0.05)
            s.dtr = False
            s.rts = True
            time.sleep(0.1)
            s.rts = False
            buf = b""
            t0 = time.time()
            while time.time() - t0 < 4.0:
                s.write(b"\x03\x03")
                time.sleep(0.02)
                chunk = s.read(512)
                if chunk:
                    buf += chunk
                    if b">>>" in buf:
                        break
            s.close()
        except Exception:
            pass
        try:
            subprocess.run([mpremote_bin, "connect", port, "mkdir", ":assets"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
            subprocess.run(
                [
                    mpremote_bin,
                    "connect",
                    port,
                    "cp",
                    cfg_out_path,
                    ":config.json",
                    "+",
                    "cp",
                    os.path.join(SD_STAGING_DIR, "inkplate_app.py"),
                    ":inkplate_app.py",
                    "+",
                    "cp",
                    os.path.join(SD_STAGING_DIR, "main.py"),
                    ":main.py",
                    "+",
                    "cp",
                    os.path.join(SD_STAGING_DIR, "rle_cache.py"),
                    ":rle_cache.py",
                    "+",
                    "cp",
                    png_path,
                    ":assets/base_blueprint.png",
                    "+",
                    "cp",
                    page2_png_path,
                    ":assets/base_page2.png",
                    "+",
                    "cp",
                    font_py_path,
                    ":assets/display_fonts.py",
                ],
                check=True,
                timeout=300,
            )
            print(f"      Successfully flashed to {port}!")
        except Exception as e:
            print(f"      Note: Could not auto-flash {port} ({e}).")
    else:
        print("[5/5] Inkplate 10 USB serial port not detected; skipped internal flash deploy.")


if __name__ == "__main__":
    main()
