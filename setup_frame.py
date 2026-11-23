#!/usr/bin/env python3
"""
Rivian Live Art Frame — Interactive Setup Wizard
Automatically detects and prepares your Inkplate 10, then guides you through:
  1. Connecting your Rivian account (with 2FA; password is never stored)
  2. Entering Wi-Fi & display preferences
  3. Uploading everything to the Inkplate 10 and rebooting it
"""

import argparse
import getpass
import json
import os
import subprocess
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore")

try:
    import requests
except ImportError:
    print("Missing required packages. Please run: pip install -r requirements.txt")
    sys.exit(1)

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")
TEST_CONFIG_PATH = os.path.join(REPO_ROOT, "local", "test_config.json")
DEVICE_DIR = os.path.join(REPO_ROOT, "device")
DEVICE_CONFIG_PATH = os.path.join(DEVICE_DIR, "config.json")
DEVICE_ASSETS_DIR = os.path.join(DEVICE_DIR, "assets")

sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import vehicles  # noqa: E402  (vehicle profiles shared with the asset tools)
FIRMWARE_PATH = os.path.join(REPO_ROOT, "firmware", "inkplate-firmware.bin")

RIVIAN_URL = "https://rivian.com/api/gql/gateway/graphql"
RIVIAN_HEADERS = {
    "User-Agent": "RivianApp/707 CFNetwork/1237 Darwin/20.4.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": "com.rivian.ios.consumer-apollo-ios",
}


class Spinner:
    """Simple terminal spinner so users always know the script is busy working."""

    FRAMES = ("|", "/", "-", "\\")

    def __init__(self, message: str):
        self.message = message
        self._stop = threading.Event()
        self._thread = None
        self._start_t = 0.0

    def _run(self):
        idx = 0
        while not self._stop.is_set():
            elapsed = int(time.time() - self._start_t)
            frame = self.FRAMES[idx % len(self.FRAMES)]
            suffix = f" ({elapsed}s)" if elapsed >= 3 else ""
            sys.stdout.write(f"\r  {frame} {self.message}{suffix}   ")
            sys.stdout.flush()
            idx += 1
            self._stop.wait(0.12)

    def __enter__(self):
        self._start_t = time.time()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop.set()
        if self._thread:
            self._thread.join()
        sys.stdout.write("\r" + " " * (len(self.message) + 20) + "\r")
        sys.stdout.flush()


def banner(title: str) -> None:
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


def prompt_str(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default != "" else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val != "" else default


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a whole number (e.g. -5, 30, 23).")


def prompt_yes_no(label: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"{label} [{hint}]: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes")


def local_utc_offset_hours() -> int:
    try:
        offset_sec = time.localtime().tm_gmtoff
        return int(round(offset_sec / 3600))
    except Exception:
        return -5


def load_existing_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def vehicle_key(cfg: dict) -> str:
    """The vehicle profile this frame is set up for (defaults to the R2)."""
    return vehicles.normalize(cfg.get("vehicle_model")) or vehicles.DEFAULT_VEHICLE


def prompt_vehicle(cfg: dict) -> str:
    """Asks which vehicle blueprint to use, defaulting to the one detected on the Rivian account."""
    keys = list(vehicles.VEHICLES)
    default = vehicle_key(cfg)
    while True:
        raw = prompt_str(f"  Vehicle blueprint ({' / '.join(keys)})", default)
        key = vehicles.normalize(raw)
        if key:
            return key
        print(f"  Please enter one of: {', '.join(keys)}")


def text_default(cfg: dict, field: str, key: str) -> str:
    """Keeps the user's saved text, unless it is just another vehicle's default."""
    saved = cfg.get(field)
    other_defaults = {v[field] for k, v in vehicles.VEHICLES.items() if k != key}
    if not saved or saved in other_defaults:
        return vehicles.profile(key)[field]
    return saved


def save_configs(cfg: dict, dry_run: bool = False) -> None:
    if dry_run:
        os.makedirs(os.path.dirname(TEST_CONFIG_PATH), exist_ok=True)
        with open(TEST_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
        return

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4)
    os.makedirs(DEVICE_DIR, exist_ok=True)
    device_cfg = {
        "wifi_ssid": cfg.get("wifi_ssid", ""),
        "wifi_password": cfg.get("wifi_password", ""),
        "wifi_bssid": cfg.get("wifi_bssid", None),
        "wifi_channel": cfg.get("wifi_channel", None),
        "refresh_interval_minutes": cfg.get("refresh_interval_minutes", 30),
        "timezone_offset_hours": cfg.get("timezone_offset_hours", -5),
        "quiet_hours_start": cfg.get("quiet_hours_start", 23),
        "quiet_hours_end": cfg.get("quiet_hours_end", 5),
        "low_battery_threshold": cfg.get("low_battery_threshold", 15),
        "low_tire_psi_threshold": cfg.get("low_tire_psi_threshold", 32.0),
        "vehicle_id": cfg.get("vehicle_id", ""),
        "vehicle_name": cfg.get("vehicle_name", ""),
        "vehicle_model": vehicle_key(cfg),
        "footer_edition": cfg.get("footer_edition") or vehicles.profile(vehicle_key(cfg))["footer_edition"],
        "drawing_number": cfg.get("drawing_number") or vehicles.profile(vehicle_key(cfg))["drawing_number"],
        "revision": cfg.get("revision", "REV 1.0"),
        "charger_card_first": bool(vehicles.profile(vehicle_key(cfg)).get("charger_card_first", False)),
        "active_page": cfg.get("active_page", 1),
        "actual_general_assembly_date": cfg.get("actual_general_assembly_date", ""),
        "refresh_token": cfg.get("refresh_token"),
        "user_session_token": cfg.get("user_session_token"),
        "access_token": cfg.get("access_token"),
        "csrf_token": cfg.get("csrf_token"),
        "app_session_token": cfg.get("app_session_token"),
        "last_sync_epoch": cfg.get("last_sync_epoch"),
        "last_sync_time_str": cfg.get("last_sync_time_str"),
        "last_telemetry": cfg.get("last_telemetry"),
    }
    with open(DEVICE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(device_cfg, f, indent=4)


# ------------------------------------------------------------------------------
# Automatic Hardware Detection & MicroPython Preparation
# ------------------------------------------------------------------------------
def list_candidate_ports():
    try:
        from serial.tools import list_ports
    except ImportError:
        return [], []

    all_ports = list(list_ports.comports())
    likely = []
    for p in all_ports:
        desc = f"{p.description or ''} {p.manufacturer or ''} {p.device or ''}".lower()
        if any(k in desc for k in ("cp210", "ch340", "ch910", "usbserial", "slab_usbtouart", "ttyusb", "ftdi", "usb-serial")):
            likely.append(p)
    return likely, all_ports


def detect_inkplate_port():
    while True:
        likely, all_ports = list_candidate_ports()
        if len(likely) == 1:
            p = likely[0]
            print(f"  ✓ Found Inkplate 10 on {p.device}")
            return p.device
        if len(likely) > 1:
            print("\n  Multiple USB-serial devices detected:")
            for idx, p in enumerate(likely, 1):
                print(f"    [{idx}] {p.device} — {p.description}")
            choice = prompt_int("  Select your Inkplate 10 port number", 1)
            if 1 <= choice <= len(likely):
                return likely[choice - 1].device
        else:
            print("\n  Inkplate 10 not detected yet.")
            print("  Please connect your Inkplate 10 with the included USB data cable")
            print("  and make sure it is turned ON (you should see a blue LED).")
            if all_ports:
                print("\n  Other serial ports seen on this computer:")
                for p in all_ports:
                    print(f"    - {p.device} ({p.description})")

        action = input("\n  Press [Enter] to check again, type a port (e.g. COM3), or type 'skip': ").strip()
        if action.lower() == "skip":
            return None
        if action:
            return action


def wake_micropython_repl(port: str, timeout_sec: float = 3.5) -> bool:
    """Pulses RTS to wake ESP32 from deep sleep and interrupts main.py into MicroPython REPL."""
    try:
        import serial
    except ImportError:
        return False

    try:
        s = serial.Serial(port, 115200, timeout=0.05)
        s.dtr = False
        s.rts = True
        time.sleep(0.1)
        s.rts = False
        buf = b""
        t0 = time.time()
        while time.time() - t0 < timeout_sec:
            s.write(b"\x03\x03")
            time.sleep(0.03)
            chunk = s.read(512)
            if chunk:
                buf += chunk
                if b">>>" in buf:
                    s.close()
                    return True
        s.close()
    except Exception:
        pass
    return False


def ensure_micropython(port: str, dry_run: bool = False) -> None:
    if dry_run:
        with Spinner(f"Checking MicroPython on {port}..."):
            time.sleep(1.0)
        print(f"  [DRY RUN] MicroPython not detected on {port} (simulated fresh board).")
        cmd = [
            sys.executable, "-m", "esptool", "--chip", "esp32",
            "--port", port, "--baud", "460800", "write_flash", "-z", "0x1000", FIRMWARE_PATH,
        ]
        with Spinner("Flashing MicroPython firmware to Inkplate 10..."):
            time.sleep(1.5)
        print(f"  [DRY RUN] Would run: {' '.join(cmd)}")
        return

    with Spinner(f"Checking MicroPython on {port}..."):
        has_mp = wake_micropython_repl(port)

    if has_mp:
        print("  ✓ MicroPython is already installed on your Inkplate 10.")
        return

    if not os.path.exists(FIRMWARE_PATH):
        print(f"  ERROR: Firmware file not found at {FIRMWARE_PATH}")
        sys.exit(1)

    print("  MicroPython not found on this board — flashing firmware automatically (takes ~20s)...")
    cmd = [
        sys.executable,
        "-m",
        "esptool",
        "--chip",
        "esp32",
        "--port",
        port,
        "--baud",
        "460800",
        "write_flash",
        "-z",
        "0x1000",
        FIRMWARE_PATH,
    ]
    with Spinner("Flashing MicroPython firmware (please keep Inkplate plugged in)..."):
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if res.returncode != 0:
        print("\n  [!] Firmware flash failed:")
        print(res.stdout[-600:] if res.stdout else "")
        print("  Try unplugging/replugging the Inkplate and running setup_frame.py again.")
        sys.exit(1)

    with Spinner("Waiting for Inkplate 10 to boot MicroPython..."):
        time.sleep(2.5)
        wake_micropython_repl(port, timeout_sec=5.0)
    print("  ✓ MicroPython firmware flashed and ready.")


# ------------------------------------------------------------------------------
# Step 1/3: Rivian Authentication & Vehicle Discovery
# ------------------------------------------------------------------------------
def rivian_login(cfg: dict, dry_run: bool = False) -> dict:
    banner("Step 1/3: Connect Your Rivian Account")
    print("Security note: Your Rivian password is used once to sign in and is")
    print("NEVER stored on your computer or on the Inkplate 10 (only session tokens).")
    print("We recommend using a dedicated secondary Rivian driver account.")

    if not dry_run and cfg.get("refresh_token") and cfg.get("user_session_token"):
        print("\n  Existing Rivian login tokens found in config.json.")
        if prompt_yes_no("  Keep existing Rivian login?", default=True):
            return cfg

    if dry_run:
        email = input("\n  Rivian Email: ").strip() or "test+rivian@example.com"
        print("  (Characters are hidden while typing/pasting your password.)")
        _ = getpass.getpass("  Rivian Password: ").strip()
        with Spinner(f"Signing in to Rivian as {email}..."):
            time.sleep(1.2)
        print(f"  [DRY RUN] Would authenticate '{email}' via Rivian API (with 2FA if prompted).")
        print("  [DRY RUN] Would query your account to auto-discover your vehicle name & Born On date.")
        cfg["access_token"] = "DRY_RUN_ACCESS_TOKEN"
        cfg["refresh_token"] = "DRY_RUN_REFRESH_TOKEN"
        cfg["user_session_token"] = "DRY_RUN_USER_SESSION_TOKEN"
        cfg["csrf_token"] = "DRY_RUN_CSRF_TOKEN"
        cfg["app_session_token"] = "DRY_RUN_APP_SESSION_TOKEN"
        cfg["vehicle_id"] = "01-DRY-RUN-VEHICLE"
        cfg["vehicle_name"] = "Test R2"
        cfg["actual_general_assembly_date"] = "2026-06-15"
        print(f"  ✓ [DRY RUN] Simulated vehicle: {cfg['vehicle_name']} (Born On: {cfg['actual_general_assembly_date']})")
        return cfg

    with Spinner("Connecting to Rivian..."):
        csrf_payload = {
            "operationName": "CreateCSRFToken",
            "query": "mutation CreateCSRFToken { createCsrfToken { __typename csrfToken appSessionToken } }",
            "variables": None,
        }
        resp = requests.post(RIVIAN_URL, json=csrf_payload, headers=RIVIAN_HEADERS, timeout=20)
        resp.raise_for_status()
        csrf_data = (resp.json().get("data") or {}).get("createCsrfToken") or {}
        csrf_token = csrf_data.get("csrfToken")
        app_sess = csrf_data.get("appSessionToken")
    if not csrf_token or not app_sess:
        raise RuntimeError("Failed to retrieve CSRF tokens from Rivian.")

    headers = RIVIAN_HEADERS.copy()
    headers["Csrf-Token"] = csrf_token
    headers["A-Sess"] = app_sess

    email = input("\n  Rivian Email: ").strip()
    print("  (Characters are hidden while typing/pasting your password.)")
    password = getpass.getpass("  Rivian Password: ").strip()
    if not email or not password:
        raise RuntimeError("Email and password are required.")

    clean_email = json.dumps(email)
    clean_password = json.dumps(password)
    login_query = (
        f"mutation Login {{ login(email: {clean_email}, password: {clean_password}) {{"
        " __typename ... on MobileLoginResponse { accessToken refreshToken userSessionToken }"
        " ... on MobileMFALoginResponse { otpToken } } }"
    )
    with Spinner("Signing in to Rivian..."):
        login_resp = requests.post(
            RIVIAN_URL, json={"operationName": "Login", "query": login_query}, headers=headers, timeout=20
        ).json()
    if "errors" in login_resp:
        raise RuntimeError(f"Login failed: {login_resp['errors'][0].get('message', 'Unknown error')}")

    login_data = (login_resp.get("data") or {}).get("login") or {}
    typename = login_data.get("__typename")

    if typename == "MobileMFALoginResponse":
        otp_token = login_data.get("otpToken")
        print("\n  [!] 2FA Required — check your SMS or email for the 6-digit code.")
        otp_code = input("  Enter 6-digit code: ").strip()
        otp_query = (
            f"mutation LoginWithOTP {{ loginWithOTP(email: {clean_email}, otpCode: {json.dumps(otp_code)},"
            f" otpToken: {json.dumps(otp_token)}) {{ __typename"
            " ... on MobileLoginResponse { accessToken refreshToken userSessionToken } } }"
        )
        with Spinner("Verifying 2FA code..."):
            otp_resp = requests.post(
                RIVIAN_URL, json={"operationName": "LoginWithOTP", "query": otp_query}, headers=headers, timeout=20
            ).json()
        if "errors" in otp_resp:
            raise RuntimeError(f"2FA verification failed: {otp_resp['errors'][0].get('message', 'Unknown error')}")
        session_data = (otp_resp.get("data") or {}).get("loginWithOTP") or {}
    elif typename == "MobileLoginResponse":
        session_data = login_data
    else:
        raise RuntimeError(f"Unexpected login response: {login_resp}")

    cfg["access_token"] = session_data.get("accessToken")
    cfg["refresh_token"] = session_data.get("refreshToken")
    cfg["user_session_token"] = session_data.get("userSessionToken")
    cfg["csrf_token"] = csrf_token
    cfg["app_session_token"] = app_sess
    print("  ✓ Signed in to Rivian (password discarded, session tokens saved).")

    # Auto-discover vehicles
    try:
        headers["U-Sess"] = cfg["user_session_token"]
        headers["Authorization"] = f"Bearer {cfg['access_token']}"
        u_query = (
            "query getUserInfo { currentUser { vehicles {"
            " id name vehicle { make model modelYear actualGeneralAssemblyDate } } } }"
        )
        with Spinner("Looking up your Rivian vehicle..."):
            u_resp = requests.post(
                RIVIAN_URL,
                json={"operationName": "getUserInfo", "query": u_query, "variables": None},
                headers=headers,
                timeout=20,
            ).json()
        vehicles = ((u_resp.get("data") or {}).get("currentUser") or {}).get("vehicles") or []
        if vehicles:
            chosen = vehicles[0]
            if len(vehicles) > 1:
                print("\n  Multiple Rivian vehicles found on your account:")
                for idx, v in enumerate(vehicles, 1):
                    vi = v.get("vehicle") or {}
                    print(f"    [{idx}] {v.get('name') or 'Unnamed'} ({vi.get('modelYear', '')} {vi.get('model', '')}) — ID: {v.get('id')}")
                sel = prompt_int("  Which vehicle should this frame display?", 1)
                if 1 <= sel <= len(vehicles):
                    chosen = vehicles[sel - 1]
            vi = chosen.get("vehicle") or {}
            cfg["vehicle_id"] = str(chosen.get("id") or "")
            cfg["vehicle_name"] = str(chosen.get("name") or "")
            cfg["actual_general_assembly_date"] = str(vi.get("actualGeneralAssemblyDate") or "")
            detected = vehicles.normalize(vi.get("model"))
            if detected:
                cfg["vehicle_model"] = detected
            elif vi.get("model"):
                print(f"  Note: No blueprint for the {vi.get('model')} yet; you can pick the closest one in the next step.")
            print(f"  ✓ Selected vehicle: {cfg['vehicle_name'] or vi.get('model', 'Rivian')} (Born On: {cfg['actual_general_assembly_date'] or 'N/A'})")
    except Exception as e:
        print(f"  Note: Could not auto-list vehicles right now ({e}); the frame will auto-discover on first boot.")

    return cfg


# ------------------------------------------------------------------------------
# Step 2/3: Wi-Fi & Display Preferences
# ------------------------------------------------------------------------------
def configure_preferences(cfg: dict, dry_run: bool = False) -> dict:
    banner("Step 2/3: Wi-Fi & Display Settings")
    print("Note: The Inkplate 10 connects to 2.4 GHz Wi-Fi networks.\n")

    cur_ssid = cfg.get("wifi_ssid") or ""
    if cur_ssid == "YOUR_WIFI_SSID":
        cur_ssid = ""
    cfg["wifi_ssid"] = prompt_str("  2.4 GHz Wi-Fi Network Name (SSID)", cur_ssid)

    cur_pw = cfg.get("wifi_password") or ""
    if cur_pw == "YOUR_WIFI_PASSWORD":
        cur_pw = ""
    if cur_pw:
        new_pw = getpass.getpass("  Wi-Fi Password (press Enter to keep saved password): ").strip()
        cfg["wifi_password"] = new_pw if new_pw else cur_pw
    else:
        cfg["wifi_password"] = getpass.getpass("  Wi-Fi Password: ").strip()

    default_tz = int(cfg.get("timezone_offset_hours", local_utc_offset_hours()))
    cfg["timezone_offset_hours"] = prompt_int(
        "  UTC Timezone Offset in hours (e.g. EST=-5, CST=-6, MST=-7, PST=-8)",
        default_tz,
    )

    cfg["refresh_interval_minutes"] = prompt_int(
        "  Refresh interval in minutes",
        int(cfg.get("refresh_interval_minutes", 30)),
    )
    cfg["quiet_hours_start"] = prompt_int(
        "  Quiet hours start (24h, e.g. 23 = 11 PM; pauses overnight updates)",
        int(cfg.get("quiet_hours_start", 23)),
    )
    cfg["quiet_hours_end"] = prompt_int(
        "  Quiet hours end (24h, e.g. 5 = 5 AM)",
        int(cfg.get("quiet_hours_end", 5)),
    )

    print("\n  Which Rivian is this frame for? (press Enter to accept the detected model)")
    cfg["vehicle_model"] = prompt_vehicle(cfg)

    print("\n  Blueprint footer & header text (press Enter to accept defaults):")
    cfg["footer_edition"] = prompt_str("  Footer edition label", text_default(cfg, "footer_edition", cfg["vehicle_model"]))
    cfg["drawing_number"] = prompt_str("  Drawing number (top-right)", text_default(cfg, "drawing_number", cfg["vehicle_model"]))
    cfg["revision"] = prompt_str("  Revision label (top-right)", cfg.get("revision") or "REV 1.0")

    save_configs(cfg, dry_run=dry_run)
    if dry_run:
        print(f"\n  [DRY RUN] Saved test configuration to {os.path.relpath(TEST_CONFIG_PATH, REPO_ROOT)} (real config.json untouched).")
    else:
        print(f"\n  ✓ Saved configuration to {os.path.relpath(CONFIG_PATH, REPO_ROOT)}.")
    return cfg


# ------------------------------------------------------------------------------
# Step 3/3: Upload App & Assets to Inkplate 10
# ------------------------------------------------------------------------------
def deploy_to_inkplate(port: str, dry_run: bool = False, vehicle: str = vehicles.DEFAULT_VEHICLE) -> None:
    banner("Step 3/3: Deploy to Inkplate 10")
    blueprint_dir = vehicles.assets_dir(vehicle)
    print(f"Blueprint: {vehicles.profile(vehicle)['name']}")
    files = [
        (TEST_CONFIG_PATH if dry_run else DEVICE_CONFIG_PATH, ":config.json"),
        (os.path.join(DEVICE_DIR, "inkplate_app.py"), ":inkplate_app.py"),
        (os.path.join(DEVICE_DIR, "main.py"), ":main.py"),
        (os.path.join(DEVICE_DIR, "rle_cache.py"), ":rle_cache.py"),
        (os.path.join(blueprint_dir, "base_blueprint.png"), ":assets/base_blueprint.png"),
        (os.path.join(blueprint_dir, "base_page2.png"), ":assets/base_page2.png"),
        (os.path.join(DEVICE_ASSETS_DIR, "display_fonts.py"), ":assets/display_fonts.py"),
    ]
    for src, _ in files:
        if not os.path.exists(src):
            print(f"  ERROR: Missing required file: {src}")
            if src.startswith(blueprint_dir):
                print(f"  Build this vehicle's blueprints with: python tools/build_assets.py --vehicle {vehicle} --build-only")
            sys.exit(1)

    if dry_run:
        with Spinner(f"Uploading app & blueprint assets to {port}..."):
            time.sleep(1.5)
        print(f"  [DRY RUN] Would wake MicroPython REPL on {port} and ensure :assets exists.")
        print(f"  [DRY RUN] Would upload {len(files)} files via mpremote:")
        for src, dst in files:
            print(f"    {os.path.relpath(src, REPO_ROOT)}  ->  {dst}")
        print("  [DRY RUN] Would clear /cache_p1.rle and /cache_p2.rle and reset Inkplate 10.")
        return

    print(f"Uploading app and blueprint assets to {port} (takes ~2 minutes — please keep plugged in)...")
    with Spinner("Waking Inkplate 10..."):
        wake_micropython_repl(port, timeout_sec=4.0)
        subprocess.run(
            [sys.executable, "-m", "mpremote", "connect", port, "mkdir", ":assets"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )

    cmd = [sys.executable, "-m", "mpremote", "connect", port]
    for idx, (src, dst) in enumerate(files):
        if idx > 0:
            cmd.append("+")
        cmd.extend(["cp", src, dst])
    cmd.extend([
        "+",
        "exec",
        "import os\nfor cf in ('/cache_p1.rle','/cache_p2.rle'):\n try: os.remove(cf)\n except OSError: pass\n",
        "+",
        "reset",
    ])

    with Spinner("Uploading files to Inkplate 10 (this takes ~2 minutes)..."):
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300)
    if res.returncode != 0:
        print("\n  [!] Upload failed:")
        print(res.stdout[-600:] if res.stdout else "")
        print("  Check the USB cable and try running setup_frame.py again.")
        sys.exit(1)
    print("  ✓ Upload complete and Inkplate 10 rebooted!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive setup wizard for the Rivian Live Art Frame.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run through all prompts and save local/test_config.json without flashing or calling Rivian APIs.",
    )
    parser.add_argument(
        "--vehicle",
        choices=list(vehicles.VEHICLES),
        help="Vehicle blueprint to set up (default: the model detected on your Rivian account, else r2).",
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    title = "Rivian Live Art Frame — Setup Wizard [DRY RUN]" if dry_run else "Rivian Live Art Frame — Setup Wizard"
    banner(title)
    if dry_run:
        print("DRY RUN MODE: No files on your Inkplate 10 or real config.json will be modified.")
        print(f"Test answers will be written to: {os.path.relpath(TEST_CONFIG_PATH, REPO_ROOT)}\n")
    else:
        print("Welcome! This wizard will set up your Inkplate 10 in about 5 minutes.\n")

    cfg = {} if dry_run else load_existing_config()

    # Automatically detect Inkplate 10 and ensure MicroPython is flashed
    port = detect_inkplate_port()
    if port:
        ensure_micropython(port, dry_run=dry_run)

    try:
        cfg = rivian_login(cfg, dry_run=dry_run)
    except Exception as e:
        print(f"\n  [!] Rivian sign-in error: {e}")
        sys.exit(1)

    if args.vehicle:
        cfg["vehicle_model"] = args.vehicle

    cfg = configure_preferences(cfg, dry_run=dry_run)

    if port:
        deploy_to_inkplate(port, dry_run=dry_run, vehicle=vehicle_key(cfg))
        if dry_run:
            banner("Dry Run Complete!")
            print(f"Review your generated test config at: {os.path.relpath(TEST_CONFIG_PATH, REPO_ROOT)}")
            print("Your real config.json and Inkplate 10 were NOT modified.")
        else:
            banner("All Done! Your Rivian Live Art Frame Is Booting!")
            print("Within ~30 seconds, your Inkplate 10 will connect to Wi-Fi, fetch")
            print("live telemetry from your Rivian, and refresh the e-ink display.")
            print("\nTips:")
            print("  • Press the WAKE button on the left side to flip between Page 1")
            print("    (Schematic View) and Page 2 (14-Day History & Analytics).")
            print("  • Re-run 'python setup_frame.py' anytime to update Wi-Fi or settings.")
    else:
        banner("Configuration Saved!")
        target = TEST_CONFIG_PATH if dry_run else CONFIG_PATH
        print(f"You skipped USB detection, so config was saved to {os.path.relpath(target, REPO_ROOT)}.")
        print("Plug in your Inkplate 10 and run 'python setup_frame.py' again to flash it.")


if __name__ == "__main__":
    main()
