#!/usr/bin/env python3
"""
Vehicle profiles for the Rivian Live Art Frame.

Every tool and the setup wizard pick the vehicle the same way, in this order:

1. ``--vehicle <key>`` on the command line
2. the ``FRAME_VEHICLE`` environment variable
3. ``"vehicle_model"`` in config.json
4. the default (R2)

Each vehicle keeps its pre-built device assets in its own folder, so building
one vehicle never overwrites another's. The R2 stays in ``device/assets/``;
other vehicles live in ``device/assets/<key>/``. On the Inkplate itself the
files always land at the same paths, so the device app needs no changes.

Only the standard library is used here so the setup wizard can import it.
"""

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEVICE_ASSETS_DIR = os.path.join(REPO_ROOT, "device", "assets")
DEFAULT_VEHICLE = "r2"

# Page 1 layout values (wireframe and callout targets) for the R2 are the base
# values in tools/generate_mockup.py; a profile only lists what differs.
VEHICLES = {
    "r2": {
        "name": "RIVIAN R2",
        "footer_edition": "RIVIAN R2 LAUNCH EDITION",
        "drawing_number": "R2-EV-01",
        "charger_card_first": False,
    },
    "r1t": {
        "name": "RIVIAN R1T",
        "footer_edition": "RIVIAN R1T",
        "drawing_number": "R1T-EV-01",
        # The charge port is on the front driver-side fender, so the Charger card
        # moves to the far left and Battery Level takes its place on the right.
        "charger_card_first": True,
        "wireframe_file": "assets/wireframes/r1t_wireframe.png",
        # Wheel centres on the canvas are (210, 645) and (943, 645), radius ~95.
        "terminal_dots": {
            "odometer":   (436, 436),  # Instrument cluster, behind the steering wheel
            "climate":    (505, 400),  # Middle of driver's window
            "battery12v": (720, 637),  # Battery pack between the frame rails, under the cab
            "charger":    (98, 527),   # Charge port door: panel below the headlight, front driver side

            "tire_fl":    (296, 672),  # Front L -> Front tire sidewall
            "tire_fr":    (275, 708),  # Front R -> Lower front tire
            "tire_rl":    (878, 708),  # Rear L  -> Lower rear tire
            "tire_rr":    (857, 672),  # Rear R  -> Rear tire sidewall
        },
    },
}


def normalize(value) -> str:
    """Maps a user- or API-supplied model string ('R1T', 'Rivian R2', ...) to a profile key, or ''."""
    text = str(value or "").strip().lower().replace(" ", "").replace("-", "")
    for key in sorted(VEHICLES, key=len, reverse=True):
        if text == key or text.endswith(key):
            return key
    return ""


def _from_argv(argv) -> str:
    for i, arg in enumerate(argv):
        if arg == "--vehicle" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--vehicle="):
            return arg.split("=", 1)[1]
    return ""


def _from_config() -> str:
    try:
        with open(os.path.join(REPO_ROOT, "config.json"), "r", encoding="utf-8") as f:
            return str(json.load(f).get("vehicle_model") or "")
    except Exception:
        return ""


def selected_vehicle(argv=None) -> str:
    """Returns the active vehicle key, exiting with a clear message if an unknown one was requested."""
    argv = sys.argv[1:] if argv is None else argv
    for source, raw in (
        ("--vehicle", _from_argv(argv)),
        ("FRAME_VEHICLE", os.environ.get("FRAME_VEHICLE", "")),
        ("config.json vehicle_model", _from_config()),
    ):
        if not str(raw).strip():
            continue
        key = normalize(raw)
        if not key:
            raise SystemExit(f"Unknown vehicle '{raw}' from {source}. Choose one of: {', '.join(VEHICLES)}")
        return key
    return DEFAULT_VEHICLE


def profile(key: str) -> dict:
    return VEHICLES[key]


def assets_dir(key: str) -> str:
    """Folder holding a vehicle's pre-built base blueprints (R2 keeps the original location)."""
    return DEVICE_ASSETS_DIR if key == DEFAULT_VEHICLE else os.path.join(DEVICE_ASSETS_DIR, key)
