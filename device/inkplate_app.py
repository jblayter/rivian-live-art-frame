"""
Rivian R2 Live Art Frame - MicroPython Application for Inkplate 10 (1200x820, 3-Bit Grayscale)
Runs from /sd/inkplate_app.py on the Inkplate 10 ESP32.
"""

import binascii
import gc
import json
import os
import socket
import ssl
import struct
import sys
import time

try:
    import urequests as requests
except ImportError:
    import requests

try:
    import urandom
except ImportError:
    import random as urandom

URL = "https://rivian.com/api/gql/gateway/graphql"
WS_HOST = "api.rivian.com"
WS_PATH = "/gql-consumer-subscriptions/graphql"

BASE_HEADERS = {
    "User-Agent": "RivianApp/707 CFNetwork/1237 Darwin/20.4.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": "com.rivian.ios.consumer-apollo-ios",
}

BAR_TO_PSI = 14.5037738
KM_TO_MILES = 0.621371
METERS_TO_MILES = 0.000621371


def _rand_bytes(n: int) -> bytes:
    try:
        return os.urandom(n)
    except AttributeError:
        return bytes(urandom.getrandbits(8) for _ in range(n))


def _uuid4_str() -> str:
    b = bytearray(_rand_bytes(16))
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    h = binascii.hexlify(b).decode("ascii")
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _resolve_path(rel_path: str) -> str:
    """Prefers internal flash (e.g. /config.json, /assets/...) and falls back to /sd or local dir."""
    for prefix in ("", "/sd", "device"):
        cand = (prefix + rel_path) if rel_path.startswith("/") else (prefix + "/" + rel_path)
        try:
            os.stat(cand)
            return cand
        except OSError:
            pass
    return rel_path


def load_sd_config(path: str = None) -> dict:
    target = path or _resolve_path("/config.json")
    with open(target, "r") as f:
        return json.load(f)


def save_sd_config_atomic(config: dict, path: str = None) -> None:
    target = path or _resolve_path("/config.json")
    tmp_path = target + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(config, f)
    try:
        os.remove(target)
    except OSError:
        pass
    os.rename(tmp_path, target)


def connect_wifi(config: dict, timeout_sec: int = 15) -> bool:
    import network

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        return True

    ssid = config.get("wifi_ssid")
    password = config.get("wifi_password")
    bssid_str = config.get("wifi_bssid")

    bssid_bytes = None
    if bssid_str and ":" in bssid_str:
        try:
            bssid_bytes = bytes(int(x, 16) for x in bssid_str.split(":"))
        except Exception:
            bssid_bytes = None

    if bssid_bytes:
        wlan.connect(ssid, password, bssid=bssid_bytes)
    else:
        wlan.connect(ssid, password)

    deadline = time.time() + timeout_sec
    while not wlan.isconnected() and time.time() < deadline:
        time.sleep_ms(100)

    return wlan.isconnected()


def sync_ntp_time(tz_offset_hours: int = -5, config: dict = None) -> str:
    try:
        import ntptime

        ntptime.settime()
    except Exception:
        pass

    sys_epoch = int(time.time())
    cfg_epoch = int(config.get("last_sync_epoch") or 0) if isinstance(config, dict) else 0

    if sys_epoch >= 800000000:
        now_epoch = sys_epoch
        if isinstance(config, dict):
            config["last_sync_epoch"] = sys_epoch
    elif cfg_epoch >= 800000000:
        now_epoch = cfg_epoch
    else:
        now_epoch = 847982408

    t = time.localtime(now_epoch + int(tz_offset_hours * 3600))
    hour24 = t[3]
    minute = t[4]
    ampm = "AM" if hour24 < 12 else "PM"
    hour12 = hour24 % 12
    if hour12 == 0:
        hour12 = 12
    time_str = f"{hour12}:{minute:02d} {ampm}"
    if isinstance(config, dict) and sys_epoch >= 800000000:
        config["last_sync_time_str"] = time_str
    return f"TODAY  •  {time_str}"


def format_offline_sync_str(config: dict) -> str:
    """
    Formats SYNC when status is OFFLINE:
    - 'TODAY  •  H:MM AM/PM' if last sync was today
    - 'YESTERDAY' if last sync was 1 day ago
    - 'X DAYS AGO' if last sync was >= 2 days ago
    """
    tz_sec = int(config.get("timezone_offset_hours", -5)) * 3600
    last_epoch = config.get("last_sync_epoch")
    last_time = config.get("last_sync_time_str")
    now_epoch = int(time.time())
    cur_year = time.localtime(now_epoch)[0]
    if last_epoch is not None and cur_year >= 2025:
        cur_day = (now_epoch + tz_sec) // 86400
        last_day = (int(last_epoch) + tz_sec) // 86400
        days_diff = cur_day - last_day
        if days_diff <= 0:
            return f"TODAY  •  {last_time}" if last_time else "TODAY"
        elif days_diff == 1:
            return "YESTERDAY"
        else:
            return f"{days_diff} DAYS AGO"
    if last_time:
        return f"TODAY  •  {last_time}"
    return "NEVER"


def compute_sleep_ms(config: dict) -> int:
    """
    Returns deep-sleep duration in milliseconds:
    - Every refresh_interval_minutes (30 min) during active hours
    - Stays asleep between quiet_hours_start (23 = 11 PM) and quiet_hours_end (5 = 5 AM)
    """
    interval_min = int(config.get("refresh_interval_minutes", 30))
    tz_sec = int(config.get("timezone_offset_hours", -5)) * 3600
    q_start = int(config.get("quiet_hours_start", 23))
    q_end = int(config.get("quiet_hours_end", 5))

    now_epoch = int(time.time())
    if time.localtime(now_epoch)[0] < 2025:
        return interval_min * 60 * 1000

    t = time.localtime(now_epoch + tz_sec)
    cur_min = t[3] * 60 + t[4]
    next_min = cur_min + interval_min

    # If currently >= 11 PM (23:00) or next wake would land at/after 11 PM, sleep until 5:00 AM
    if t[3] >= q_start or next_min >= q_start * 60:
        mins_to_wake = (24 * 60 - cur_min) + (q_end * 60)
        return max(1, mins_to_wake) * 60 * 1000
    # If currently between midnight and 5:00 AM, sleep until 5:00 AM
    if t[3] < q_end:
        mins_to_wake = (q_end * 60) - cur_min
        return max(1, mins_to_wake) * 60 * 1000

    return interval_min * 60 * 1000


def _parse_proto_fields(data: bytes) -> list:
    i = 0
    n = len(data)
    fields = []
    while i < n:
        tag = 0
        shift = 0
        while True:
            b = data[i]
            i += 1
            tag |= (b & 0x7F) << shift
            if not (b & 0x80):
                break
            shift += 7
        field_num = tag >> 3
        wire_type = tag & 0x07
        if wire_type == 0:
            val = 0
            shift = 0
            while True:
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
        elif wire_type == 1:
            val = data[i : i + 8]
            i += 8
        elif wire_type == 2:
            length = 0
            shift = 0
            while True:
                b = data[i]
                i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            val = data[i : i + length]
            i += length
        elif wire_type == 5:
            val = data[i : i + 4]
            i += 4
        else:
            break
        fields.append((field_num, wire_type, val))
    return fields


def decode_parallax_rvm(rvm: str, b64_payload: str) -> dict:
    if not b64_payload:
        return {}
    raw = binascii.a2b_base64(b64_payload)
    out = {}

    if rvm == "dynamics.tires.state":
        pos_map = {
            1: "tirePressureFrontLeft",
            2: "tirePressureFrontRight",
            3: "tirePressureRearLeft",
            4: "tirePressureRearRight",
        }
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 2 and wtype == 2:
                tire_fields = {tf: (tw, tv) for tf, tw, tv in _parse_proto_fields(val)}
                pos = tire_fields.get(1, (0, 0))[1]
                key = pos_map.get(pos)
                if key and 3 in tire_fields and len(tire_fields[3][1]) == 8:
                    bar_val = struct.unpack("<d", tire_fields[3][1])[0]
                    out[key] = round(bar_val * BAR_TO_PSI, 1)

    elif rvm == "comfort.cabin.cabin_temperatures":
        for fnum, wtype, val in _parse_proto_fields(raw):
            if wtype == 5 and len(val) == 4:
                temp_c = round(struct.unpack("<f", val)[0], 1)
                if fnum == 2:
                    out["cabinClimateExteriorTemperature"] = temp_c
                elif fnum == 3:
                    out["cabinClimateInteriorTemperature"] = temp_c

    elif rvm == "energy.low_voltage.battery_state":
        health_map = {1: "NORMAL", 2: "LOW"}
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["battery12VState"] = health_map.get(val, "NORMAL")

    elif rvm == "energy.high_voltage.battery_state":
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 2:
                for cfnum, cwtype, cval in _parse_proto_fields(val):
                    if cfnum == 1 and cwtype == 1 and len(cval) == 8:
                        out["batteryLevel"] = round(struct.unpack("<d", cval)[0], 1)
            elif fnum == 2 and wtype == 2:
                for tfnum, twtype, tval in _parse_proto_fields(val):
                    if twtype == 5 and len(tval) == 4:
                        tc = round(struct.unpack("<f", tval)[0], 1)
                        if tfnum == 1:
                            out["batteryTemperature"] = tc
                        elif tfnum == 2:
                            out["batteryTempMax"] = tc
                        elif tfnum == 3:
                            out["batteryTempMin"] = tc

    elif rvm == "dynamics.vehicle.range":
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["distanceToEmpty"] = val

    elif rvm == "dynamics.vehicle.odometer":
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["vehicleMileage"] = val * 1000

    elif rvm == "charging.session.status":
        state_map = {
            1: "charging_ready",
            2: "charging_connecting",
            3: "charging_active",
            4: "charging_complete",
            5: "charging_scheduled",
        }
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 2 and wtype == 0:
                out["chargerState"] = state_map.get(val, "charging_ready")

    return out


def fetch_parallax_ws(user_session_token: str, vehicle_id: str, timeout_sec: int = 4) -> dict:
    target_rvms = [
        "dynamics.tires.state",
        "comfort.cabin.cabin_temperatures",
        "energy.low_voltage.battery_state",
        "energy.high_voltage.battery_state",
        "dynamics.vehicle.range",
        "dynamics.vehicle.odometer",
        "charging.session.status",
    ]
    collected = {}
    seen_rvms = set()

    addr = socket.getaddrinfo(WS_HOST, 443)[0][-1]
    raw_sock = socket.socket()
    raw_sock.settimeout(10)
    raw_sock.connect(addr)
    try:
        sock = ssl.wrap_socket(raw_sock, server_hostname=WS_HOST)
    except TypeError:
        sock = ssl.wrap_socket(raw_sock)

    try:
        ws_key = binascii.b2a_base64(_rand_bytes(16)).decode("ascii").strip()
        handshake = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {WS_HOST}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: graphql-transport-ws\r\n"
            f"User-Agent: {BASE_HEADERS['User-Agent']}\r\n\r\n"
        )
        sock.write(handshake.encode("utf-8"))

        while True:
            line = sock.readline()
            if not line or line == b"\r\n":
                break
        buf = bytearray()

        def ws_send(msg_dict: dict) -> None:
            data = json.dumps(msg_dict).encode("utf-8")
            mask_key = _rand_bytes(4)
            length = len(data)
            header = bytearray([0x81])
            if length < 126:
                header.append(0x80 | length)
            else:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            header.extend(mask_key)
            masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(data))
            sock.write(bytes(header) + masked)

        def recv_exact(n: int) -> bytes:
            nonlocal buf
            while len(buf) < n:
                chunk = sock.read(n - len(buf))
                if not chunk:
                    raise EOFError()
                buf.extend(chunk)
            out = bytes(buf[:n])
            buf = buf[n:]
            return out

        def ws_recv():
            hdr = recv_exact(2)
            b1, b2 = hdr[0], hdr[1]
            opcode = b1 & 0x0F
            masked = bool(b2 & 0x80)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", recv_exact(8))[0]
            mask_key = recv_exact(4) if masked else b""
            payload = recv_exact(length)
            if masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
            return opcode, payload

        ws_send(
            {
                "type": "connection_init",
                "payload": {
                    "client-name": BASE_HEADERS["Apollographql-Client-Name"],
                    "client-version": "1.13.0-1494",
                    "dc-cid": f"m-ios-{_uuid4_str()}",
                    "u-sess": user_session_token,
                },
            }
        )
        ws_recv()

        sub_id = _uuid4_str()
        ws_send(
            {
                "id": sub_id,
                "type": "subscribe",
                "payload": {
                    "operationName": "ParallaxMessages",
                    "query": (
                        "subscription ParallaxMessages($vehicleId: String!, $rvms: [String!]) {"
                        " parallaxMessages(vehicleId: $vehicleId, rvms: $rvms) { payload timestamp rvm } }"
                    ),
                    "variables": {"vehicleId": vehicle_id, "rvms": target_rvms},
                },
            }
        )

        deadline = time.time() + timeout_sec
        while time.time() < deadline and len(seen_rvms) < len(target_rvms):
            try:
                op, frame = ws_recv()
                if op != 1:
                    continue
                msg = json.loads(frame.decode("utf-8"))
                if msg.get("type") == "next":
                    px = msg["payload"]["data"]["parallaxMessages"]
                    rvm = px.get("rvm")
                    b64 = px.get("payload")
                    if rvm and b64 is not None:
                        seen_rvms.add(rvm)
                        collected.update(decode_parallax_rvm(rvm, b64))
            except Exception:
                break

        try:
            ws_send({"id": sub_id, "type": "complete"})
        except Exception:
            pass
    finally:
        sock.close()

    return collected


def fetch_all_r2_telemetry(config: dict) -> dict:
    # 1. Refresh access token
    headers = BASE_HEADERS.copy()
    headers["Csrf-Token"] = config["csrf_token"]
    headers["A-Sess"] = config["app_session_token"]
    headers["U-Sess"] = config["user_session_token"]

    refresh_payload = {
        "operationName": "RefreshAccessToken",
        "query": (
            "mutation RefreshAccessToken($refreshToken: String!) {"
            " refreshAccessToken(refreshToken: $refreshToken) { accessToken refreshToken } }"
        ),
        "variables": {"refreshToken": config["refresh_token"]},
    }
    r = requests.post(URL, data=json.dumps(refresh_payload), headers=headers)
    body = r.json()
    r.close()

    token_data = body["data"]["refreshAccessToken"]
    config["access_token"] = token_data["accessToken"]
    if token_data.get("refreshToken"):
        config["refresh_token"] = token_data["refreshToken"]
    save_sd_config_atomic(config)

    headers["Authorization"] = "Bearer " + config["access_token"]
    vehicle_id = str(config.get("vehicle_id") or "").strip()
    if not vehicle_id:
        u_query = (
            "query getUserInfo { currentUser { vehicles {"
            " id name vehicle { model actualGeneralAssemblyDate } } } }"
        )
        ru = requests.post(
            URL,
            data=json.dumps({"operationName": "getUserInfo", "query": u_query, "variables": None}),
            headers=headers,
        )
        u_body = ru.json()
        ru.close()
        vehicles = ((u_body.get("data") or {}).get("currentUser") or {}).get("vehicles") or []
        chosen = None
        for v in vehicles:
            v_info = v.get("vehicle") or {}
            if "R2" in str(v_info.get("model") or "").upper() or "R2" in str(v.get("name") or "").upper():
                chosen = v
                break
        if chosen is None and vehicles:
            chosen = vehicles[0]
        if not chosen or not chosen.get("id"):
            raise RuntimeError("No Rivian vehicle found on account; set 'vehicle_id' in config.json.")
        vehicle_id = str(chosen["id"])
        config["vehicle_id"] = vehicle_id
        if chosen.get("name") and not config.get("vehicle_name"):
            config["vehicle_name"] = chosen["name"]
        born_on = (chosen.get("vehicle") or {}).get("actualGeneralAssemblyDate")
        if born_on and not config.get("actual_general_assembly_date"):
            config["actual_general_assembly_date"] = born_on
        save_sd_config_atomic(config)

    # 2. Fetch Parallax WebSocket telemetry (all 7 RVMs)
    telemetry = fetch_parallax_ws(config["user_session_token"], vehicle_id)

    # 3. Fetch batteryCapacity, gnssLocation, charging schedule, & fallback fields from HTTP GetVehicleState
    vs_query = (
        "query GetVehicleState($vehicleID: String!) {"
        " vehicleState(id: $vehicleID) {"
        " gnssLocation { latitude longitude }"
        " batteryCapacity { value }"
        " batteryLevel { value } batteryLimit { value }"
        " chargerState { value } timeToEndOfCharge { value }"
        " distanceToEmpty { value } vehicleMileage { value } }"
        " getVehicle(id: $vehicleID) {"
        " chargingSchedules { startTime duration enabled } } }"
    )
    r2 = requests.post(
        URL,
        data=json.dumps(
            {
                "operationName": "GetVehicleState",
                "query": vs_query,
                "variables": {"vehicleID": vehicle_id},
            }
        ),
        headers=headers,
    )
    vs_body = r2.json()
    r2.close()
    vs = (vs_body.get("data") or {}).get("vehicleState") or {}
    for k in (
        "batteryCapacity",
        "batteryLevel",
        "batteryLimit",
        "chargerState",
        "timeToEndOfCharge",
        "distanceToEmpty",
        "vehicleMileage",
    ):
        if telemetry.get(k) is None and isinstance(vs.get(k), dict):
            telemetry[k] = vs[k].get("value")
    gv = (vs_body.get("data") or {}).get("getVehicle") or {}
    schedules = gv.get("chargingSchedules") or []
    sched_start = None
    for s_item in schedules:
        if isinstance(s_item, dict) and s_item.get("startTime") is not None:
            if s_item.get("enabled") is False:
                continue
            sched_start = s_item.get("startTime")
            break
    if sched_start is None and schedules and isinstance(schedules[0], dict):
        sched_start = schedules[0].get("startTime")
    if sched_start is not None:
        telemetry["scheduleStartTime"] = sched_start
    if telemetry.get("cabinClimateExteriorTemperature") is None:
        gnss = vs.get("gnssLocation") or {}
        lat, lon = gnss.get("latitude"), gnss.get("longitude")
        if lat is not None and lon is not None:
            try:
                w_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat:.4f}&longitude={lon:.4f}&current=temperature_2m"
                wr = requests.get(w_url)
                w_data = wr.json()
                wr.close()
                ext_c = (w_data.get("current") or {}).get("temperature_2m")
                if ext_c is not None:
                    telemetry["cabinClimateExteriorTemperature"] = round(float(ext_c), 1)
            except Exception:
                pass

    return telemetry


def _stamp_footer(display, cfg_now: dict, draw_text, measure_text=None) -> None:
    edition = str(cfg_now.get("footer_edition") or "RIVIAN R2").strip().upper()
    v_name = str(cfg_now.get("vehicle_name") or "").strip().upper()
    born_on = str(cfg_now.get("actual_general_assembly_date") or "").strip()
    parts = [edition] if edition else []
    if v_name and v_name != edition:
        parts.append(v_name)
    if born_on:
        parts.append(f"BORN ON: {born_on}")
    footer_text = "  //  ".join(parts)
    sz = 19
    if measure_text is not None and 38 + measure_text(footer_text, size=19) > 990:
        sz = 17
    draw_text(display, 38, 749, footer_text, size=sz, ink_level=0)


def render_dashboard_on_inkplate(display, telemetry: dict, sync_str: str, status: str = "ONLINE", flush: bool = True) -> None:
    for p in ("/assets", "/sd/assets"):
        if p not in sys.path:
            sys.path.append(p)
    from display_fonts import draw_reversed_text, draw_text, measure_text

    set_px = getattr(display, "write_pixel", None) or getattr(display, "draw_pixel", None) or getattr(display, "drawPixel", None)

    # 1. Load pre-rendered 1200x820 base blueprint into GS4 framebuffer
    if hasattr(display, "draw_png_from_sd"):
        display.draw_png_from_sd(_resolve_path("/assets/base_blueprint.png"), 0, 0)
    else:
        fb = display._framebuf() if hasattr(display, "_framebuf") else None
        if fb is not None:
            mv = memoryview(fb)
            with open(_resolve_path("/assets/base_blueprint.3bit"), "rb") as f:
                offset = 0
                total = 492000  # 1200 * 820 // 2 bytes
                while offset < total:
                    chunk_len = min(16384, total - offset)
                    n = f.readinto(mv[offset : offset + chunk_len])
                    if not n:
                        break
                    offset += n

    # 2. Stamp dynamic header drawing/revision, footer, & status lines (Right-aligned at X=1164)
    try:
        cfg_now = load_sd_config()
        raw_dwg = str(cfg_now.get("drawing_number") or "R2-EPD-01").strip()
        raw_rev = str(cfg_now.get("revision") or "REV 1.0").strip()
        low_batt_thresh = int(cfg_now.get("low_battery_threshold", 15))
        low_tire_thresh = float(cfg_now.get("low_tire_psi_threshold", 32.0))
    except Exception:
        cfg_now = {}
        raw_dwg = "R2-EPD-01"
        raw_rev = "REV 1.0"
        low_batt_thresh = 15
        low_tire_thresh = 32.0
    _stamp_footer(display, cfg_now, draw_text, measure_text)
    dwg_str = raw_dwg if raw_dwg.upper().startswith("DWG") else f"DWG NO. {raw_dwg}"
    rev_str = raw_rev if raw_rev.upper().startswith("REV") else f"REV {raw_rev}"
    meta_line1 = f"{dwg_str}  /  {rev_str}"
    w1 = measure_text(meta_line1, size=20)
    draw_text(display, 1164 - w1, 41, meta_line1, size=20, ink_level=0)

    status_up = status.upper()
    if status_up == "OFFLINE":
        prefix_str = "STATUS: "
        suffix_str = f"  •  SYNC: {sync_str}"
        w_prefix = measure_text(prefix_str, size=19)
        w_status = measure_text(status_up, size=19)
        w_suffix = measure_text(suffix_str, size=19)
        pad_x = 5
        total_w = w_prefix + w_status + pad_x * 2 + w_suffix
        x_cur = 1164 - total_w
        draw_text(display, x_cur, 71, prefix_str, size=19, ink_level=0)
        x_cur += w_prefix + pad_x
        draw_reversed_text(display, x_cur, 71, status_up, size=19, pad_x=pad_x, pad_y=3)
        x_cur += w_status + pad_x
        draw_text(display, x_cur, 71, suffix_str, size=19, ink_level=0)
    else:
        meta_line2 = f"STATUS: {status_up}  •  SYNC: {sync_str}"
        w2 = measure_text(meta_line2, size=19)
        draw_text(display, 1164 - w2, 71, meta_line2, size=19, ink_level=0)

    # 3. Card 01 // BATTERY LEVEL (X=36, Y=120)
    pct = max(0, min(100, int(round(float(telemetry.get("batteryLevel") or 0)))))
    dte_mi = int(round(float(telemetry.get("distanceToEmpty") or 0) * KM_TO_MILES))
    if pct < low_batt_thresh:
        draw_reversed_text(display, 36 + 10, 120 + 46, f"{pct}%", size=26, pad_x=5, pad_y=3)
    else:
        draw_text(display, 36 + 10, 120 + 46, f"{pct}%", size=26, ink_level=0)

    # Fill proportional battery bargraph + 25/50/75% scale ticks
    bar_x, bar_y, bar_w, bar_h = 36 + 82, 120 + 46, 114, 20
    fill_w = int((bar_w - 6) * (pct / 100.0))
    if hasattr(display, "fill_rect") and fill_w > 0:
        display.fill_rect(bar_x + 3, bar_y + 3, fill_w, bar_h - 6, 0)
    else:
        for by in range(bar_y + 3, bar_y + bar_h - 3):
            for bx in range(bar_x + 3, bar_x + 3 + fill_w):
                set_px(bx, by, 0)
    for tick_pct in (0.25, 0.50, 0.75):
        tx = bar_x + int(bar_w * tick_pct)
        tick_c = 7 if tx <= (bar_x + 3 + fill_w) else 0
        for by in range(bar_y + 3, bar_y + bar_h - 3):
            set_px(tx, by, tick_c)
            set_px(tx + 1, by, tick_c)
    draw_text(display, 36 + 10, 120 + 88, f"EST. RANGE: {dte_mi} MI", size=18, ink_level=0)

    # 4. Card 02 // ODOMETER (X=264, Y=120)
    odo_mi = int(round(float(telemetry.get("vehicleMileage") or 0) * METERS_TO_MILES))
    draw_text(display, 264 + 10, 120 + 46, f"{odo_mi:,} MI", size=26, ink_level=0)
    draw_text(display, 264 + 10, 120 + 88, "LIFETIME TOTAL MILES", size=18, ink_level=0)

    # 5. Card 03 // CLIMATE (X=492, Y=120)
    int_c = float(telemetry.get("cabinClimateInteriorTemperature") or 21.0)
    int_f = int(round((int_c * 9.0 / 5.0) + 32.0))
    ext_c = telemetry.get("cabinClimateExteriorTemperature")
    ext_str = (
        f"EXTERIOR: {int(round((float(ext_c) * 9.0 / 5.0) + 32.0))}°F"
        if ext_c is not None
        else f"RAW: {int_c:.1f}°C INTERIOR"
    )
    draw_text(display, 492 + 10, 120 + 46, f"{int_f}°F", size=26, ink_level=0)
    draw_text(display, 492 + 10, 120 + 88, ext_str, size=18, ink_level=0)

    # 6. Card 04 // BATTERY STATE (X=720, Y=120)
    cap_kwh = float(telemetry.get("batteryCapacity") or 90.9)
    avg_c = float(telemetry.get("batteryTemperature") or 21.0)
    avg_f = int(round((avg_c * 9.0 / 5.0) + 32.0))
    v12 = str(telemetry.get("battery12VState") or "NORMAL").upper()
    draw_text(display, 720 + 10, 120 + 46, f"{cap_kwh:.1f} kWh", size=26, ink_level=0)
    draw_text(display, 720 + 138, 120 + 49, f"{avg_f}°F", size=20, ink_level=0)
    draw_text(display, 720 + 10, 120 + 88, f"12V STATUS: {v12}", size=18, ink_level=0)

    # 7. Card 05 // CHARGER (X=948, Y=120)
    raw_chg = str(telemetry.get("chargerState") or "charging_ready").lower()
    batt_lim = telemetry.get("batteryLimit")
    lim_int = int(round(float(batt_lim))) if batt_lim is not None else None
    sched_start = telemetry.get("scheduleStartTime")
    tte = telemetry.get("timeToEndOfCharge")

    if raw_chg == "charging_active":
        chg_hero = "CHARGING"
        rem_m = int(round(float(tte))) if tte is not None else 0
        if rem_m > 0:
            rem_str = (
                f"{rem_m // 60}H {rem_m % 60}M LEFT"
                if (rem_m >= 60 and rem_m % 60 > 0)
                else (f"{rem_m // 60}H LEFT" if rem_m >= 60 else f"{rem_m}M LEFT")
            )
            chg_sub = f"TO {lim_int}% | {rem_str}" if lim_int else rem_str
        elif lim_int:
            chg_sub = f"TO {lim_int}% | ACTIVE"
        else:
            chg_sub = "ACTIVE SESSION"
    elif raw_chg == "charging_complete":
        chg_hero = "COMPLETE"
        chg_sub = f"TARGET {lim_int}% REACHED" if lim_int else "TARGET CHARGE REACHED"
    elif raw_chg == "charging_scheduled":
        chg_hero = "SCHEDULED"
        if sched_start is not None:
            sm = int(sched_start) % 1440
            h24, m = sm // 60, sm % 60
            ampm = "AM" if h24 < 12 else "PM"
            h12 = (h24 % 12) or 12
            start_str = f"{h12}:{m:02d} {ampm}"
            chg_sub = f"STARTS {start_str} (TO {lim_int}%)" if lim_int else f"STARTS AT {start_str}"
        elif lim_int:
            chg_sub = f"SCHEDULED (TO {lim_int}%)"
        else:
            chg_sub = "WAITING FOR SCHEDULE"
    else:
        chg_hero, chg_sub = "DISCONNECTED", "CHARGE PORT READY"
    draw_text(display, 948 + 10, 120 + 46, chg_hero, size=26, ink_level=0)
    chg_sub_sz = 17 if measure_text(chg_sub, size=18) > 196 else 18
    draw_text(display, 948 + 10, 120 + 88, chg_sub, size=chg_sub_sz, ink_level=0)

    # 8. Tire Pressures (Underbody Badges at Y=660)
    tire_positions = [
        (360, telemetry.get("tirePressureFrontLeft") or 38),
        (456, telemetry.get("tirePressureFrontRight") or 38),
        (566, telemetry.get("tirePressureRearLeft") or 39),
        (662, telemetry.get("tirePressureRearRight") or 39),
    ]
    for bx, psi in tire_positions:
        psi_val = float(psi)
        psi_str = f"{int(round(psi_val))} PSI"
        if psi_val < low_tire_thresh:
            draw_reversed_text(display, bx + 10, 660 + 30, psi_str, size=22, pad_x=5, pad_y=3, box_w=78)
        else:
            draw_text(display, bx + 10, 660 + 30, psi_str, size=22, ink_level=0)

    # 9. Inkplate 10 Battery Icon Fill (below ground line right-aligned next to 'INKPLATE 10', X=1128, Y=748)
    try:
        ink_v = float(display.read_battery()) if hasattr(display, "read_battery") else 4.0
    except Exception:
        ink_v = 4.0
    ink_pct = max(0, min(100, int(round((ink_v - 3.30) / (4.20 - 3.30) * 100.0))))
    ib_x, ib_y, ib_inner_w, ib_inner_h = 1128 + 3, 740 + 8 + 3, 26, 10
    ib_fill_w = max(0, min(ib_inner_w, int(round(ib_inner_w * (ink_pct / 100.0)))))
    if ib_fill_w > 0:
        if hasattr(display, "fill_rect"):
            display.fill_rect(ib_x, ib_y, ib_fill_w, ib_inner_h, 0)
        else:
            for by in range(ib_y, ib_y + ib_inner_h):
                for bx in range(ib_x, ib_x + ib_fill_w):
                    set_px(bx, by, 0)

    if flush:
        display.display()


# ==============================================================================
# TELEMETRY HISTORY LOGGING & PAGE 2 ANALYTICS RENDERER (REV 1.1)
# ==============================================================================

LOG_MAX_LINES = 504  # 14 days * 36 active-hour syncs/day


def _get_inkplate_battery(display) -> tuple:
    try:
        ink_v = float(display.read_battery()) if (display and hasattr(display, "read_battery")) else 4.03
    except Exception:
        ink_v = 4.03
    ink_pct = max(0, min(100, int(round((ink_v - 3.30) / (4.20 - 3.30) * 100.0))))
    return ink_v, ink_pct


def _build_log_record(telemetry: dict, config: dict, display=None) -> dict:
    tz_off = int(config.get("timezone_offset_hours", -5))
    sys_epoch = int(time.time())
    cfg_epoch = int(config.get("last_sync_epoch") or 0) if isinstance(config, dict) else 0
    if sys_epoch >= 800000000:
        now_epoch = sys_epoch
    elif cfg_epoch >= 800000000:
        now_epoch = cfg_epoch
    else:
        now_epoch = 848001604
    local_sec = now_epoch + tz_off * 3600
    day_idx = local_sec // 86400
    try:
        wday = time.localtime(local_sec)[6]
    except Exception:
        wday = (day_idx + 5) % 7

    soc = round(float(telemetry.get("batteryLevel") or 0), 1)
    dte = int(round(float(telemetry.get("distanceToEmpty") or 0) * KM_TO_MILES))
    odo = round(float(telemetry.get("vehicleMileage") or 0) * METERS_TO_MILES, 1)
    cap = round(float(telemetry.get("batteryCapacity") or 90.9), 1)
    tp = int(round((float(telemetry.get("batteryTemperature") or 21.0) * 9.0 / 5.0) + 32.0))
    tc = int(round((float(telemetry.get("cabinClimateInteriorTemperature") or 21.0) * 9.0 / 5.0) + 32.0))
    ext_raw = telemetry.get("cabinClimateExteriorTemperature")
    te = int(round((float(ext_raw if ext_raw is not None else 18.0) * 9.0 / 5.0) + 32.0))
    chg = str(telemetry.get("chargerState") or "charging_ready").lower()
    psi = [
        round(float(telemetry.get("tirePressureFrontLeft") or 38.0), 1),
        round(float(telemetry.get("tirePressureFrontRight") or 38.0), 1),
        round(float(telemetry.get("tirePressureRearLeft") or 38.0), 1),
        round(float(telemetry.get("tirePressureRearRight") or 38.0), 1),
    ]
    ink_v, ink_pct = _get_inkplate_battery(display)

    return {
        "t": now_epoch,
        "d": day_idx,
        "w": wday,
        "soc": soc,
        "dte": dte,
        "odo": odo,
        "cap": cap,
        "tp": tp,
        "tc": tc,
        "te": te,
        "chg": chg,
        "psi": psi,
        "iv": round(ink_v, 3),
        "ip": ink_pct,
    }


def _get_log_path() -> str:
    try:
        os.stat("/config.json")
        return "/telemetry_log.jsonl"
    except OSError:
        return _resolve_path("/telemetry_log.jsonl")


def append_telemetry_log(telemetry: dict, config: dict, display=None) -> dict:
    """Appends a compact JSONL record to /telemetry_log.jsonl and caps size at LOG_MAX_LINES."""
    log_path = _get_log_path()
    rec = _build_log_record(telemetry, config, display=display)
    line_str = json.dumps(rec) + "\n"

    existing = []
    last_t = 0
    try:
        with open(log_path, "r") as f:
            for ln in f:
                if ln.strip():
                    existing.append(ln if ln.endswith("\n") else (ln + "\n"))
        if existing:
            try:
                last_t = int(json.loads(existing[-1]).get("t") or 0)
            except Exception:
                last_t = 0
    except OSError:
        pass

    # Guard against stale RTC / config last_sync_epoch writing time-traveling entries
    if last_t > 0 and rec["t"] < last_t:
        print(f"Skipping out-of-order log record (t={rec['t']} < last_t={last_t})")
        return rec

    existing.append(line_str)
    if len(existing) > LOG_MAX_LINES:
        existing = existing[-LOG_MAX_LINES:]

    tmp_path = log_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            for ln in existing:
                f.write(ln)
        try:
            os.remove(log_path)
        except OSError:
            pass
        os.rename(tmp_path, log_path)
    except Exception as e:
        print("Log write error:", e)
    return rec


def load_telemetry_log(telemetry: dict, config: dict, display=None) -> list:
    """Loads logged records from /telemetry_log.jsonl, seeding with current telemetry if empty."""
    log_path = _get_log_path()
    records = []
    try:
        with open(log_path, "r") as f:
            for ln in f:
                s = ln.strip()
                if s:
                    try:
                        r = json.loads(s)
                        if isinstance(r, dict) and int(r.get("t", 0)) >= 800000000 and int(r.get("d", 0)) >= 9000:
                            records.append(r)
                    except Exception:
                        pass
    except OSError:
        pass

    if not records:
        rec = append_telemetry_log(telemetry or {}, config or {}, display=display)
        records = [rec]
    return records


def _fb_set_px(fb, x: int, y: int, c: int) -> None:
    if 0 <= x < 1200 and 0 <= y < 820:
        b_idx = y * 600 + (x >> 1)
        cur = fb[b_idx]
        if (x & 1) == 0:
            fb[b_idx] = (cur & 0xF0) | (c & 0x0F)
        else:
            fb[b_idx] = (cur & 0x0F) | ((c & 0x0F) << 4)


def _fb_fill_rect(fb, x0: int, y0: int, x1: int, y1: int, c: int) -> None:
    x0 = max(0, min(1199, int(x0)))
    x1 = max(0, min(1199, int(x1)))
    y0 = max(0, min(819, int(y0)))
    y1 = max(0, min(819, int(y1)))
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    cn = c & 0x0F
    byte_val = (cn << 4) | cn
    odd_start = x0 & 1
    even_end = (x1 & 1) == 0
    b_start = (x0 + 1) >> 1
    b_end = x1 >> 1
    for py in range(y0, y1 + 1):
        row_off = py * 600
        if x0 == x1:
            b_idx = row_off + (x0 >> 1)
            if odd_start:
                fb[b_idx] = (fb[b_idx] & 0x0F) | (cn << 4)
            else:
                fb[b_idx] = (fb[b_idx] & 0xF0) | cn
            continue
        if odd_start:
            b_idx = row_off + (x0 >> 1)
            fb[b_idx] = (fb[b_idx] & 0x0F) | (cn << 4)
        for bi in range(row_off + b_start, row_off + b_end):
            fb[bi] = byte_val
        if even_end:
            b_idx = row_off + (x1 >> 1)
            fb[b_idx] = (fb[b_idx] & 0xF0) | cn


def _fb_rect_box(fb, x0: int, y0: int, x1: int, y1: int, fill_c: int, outline_c: int = 0, bw: int = 2) -> None:
    _fb_fill_rect(fb, x0, y0, x1, y1, fill_c)
    if outline_c is not None and bw > 0:
        _fb_fill_rect(fb, x0, y0, x1, y0 + bw - 1, outline_c)
        _fb_fill_rect(fb, x0, y1 - bw + 1, x1, y1, outline_c)
        _fb_fill_rect(fb, x0, y0, x0 + bw - 1, y1, outline_c)
        _fb_fill_rect(fb, x1 - bw + 1, y0, x1, y1, outline_c)


def _fb_line(fb, x0: int, y0: int, x1: int, y1: int, c: int = 0, w: int = 2) -> None:
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    r_lo = -(w // 2)
    r_hi = r_lo + w
    while True:
        for oy in range(r_lo, r_hi):
            for ox in range(r_lo, r_hi):
                _fb_set_px(fb, x0 + ox, y0 + oy, c)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _fb_dashed_line(fb, x0: int, y0: int, x1: int, y1: int, dash: int = 5, gap: int = 4, c: int = 0, w: int = 1) -> None:
    import math
    dx = float(x1 - x0)
    dy = float(y1 - y0)
    dist = math.sqrt(dx * dx + dy * dy)
    if dist <= 0:
        return
    ux, uy = dx / dist, dy / dist
    pos = 0.0
    step = dash + gap
    while pos < dist:
        end_p = min(dist, pos + dash)
        sx = int(round(x0 + ux * pos))
        sy = int(round(y0 + uy * pos))
        ex = int(round(x0 + ux * end_p))
        ey = int(round(y0 + uy * end_p))
        _fb_line(fb, sx, sy, ex, ey, c=c, w=w)
        pos += step


def _fb_circle(fb, cx: int, cy: int, r: int, fill_c: int = 0, inner_c: int = None) -> None:
    cx, cy, r = int(cx), int(cy), int(r)
    r2 = r * r
    in_r = max(1, r - 2)
    in_r2 = in_r * in_r
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d2 = dx * dx + dy * dy
            if d2 <= r2:
                if inner_c is not None and d2 <= in_r2:
                    _fb_set_px(fb, cx + dx, cy + dy, inner_c)
                else:
                    _fb_set_px(fb, cx + dx, cy + dy, fill_c)


def render_page2_on_inkplate(display, telemetry: dict, sync_str: str, status: str = "ONLINE", flush: bool = True) -> None:
    """Renders Page 2 (Telemetry History & Log Analytics) onto the Inkplate 10."""
    for p in ("/assets", "/sd/assets"):
        if p not in sys.path:
            sys.path.append(p)
    from display_fonts import draw_reversed_text, draw_text, measure_text

    # 1. Load static Page 2 blueprint template into GS4 framebuffer
    display.draw_png_from_sd(_resolve_path("/assets/base_page2.png"), 0, 0)
    fb = display._framebuf()

    try:
        cfg_now = load_sd_config()
    except Exception:
        cfg_now = {}

    raw_dwg = str(cfg_now.get("drawing_number") or "R2-EPD-01").strip()
    raw_rev = str(cfg_now.get("revision") or "REV 1.0").strip()
    dwg_str = raw_dwg if raw_dwg.upper().startswith("DWG") else f"DWG NO. {raw_dwg}"
    rev_str = raw_rev if raw_rev.upper().startswith("REV") else f"REV {raw_rev}"
    _stamp_footer(display, cfg_now, draw_text, measure_text)

    records = load_telemetry_log(telemetry, cfg_now, display=display)
    n_samples = len(records)
    if n_samples >= 2:
        span_days = max(1, int(round((records[-1]["t"] - records[0]["t"]) / 86400.0)) + 1)
    else:
        span_days = 1

    # 2. Header Right Metadata (X=1164 right-aligned)
    meta_line1 = f"{dwg_str}  /  {rev_str}  •  PAGE 02/02"
    w1 = measure_text(meta_line1, size=20)
    draw_text(display, 1164 - w1, 41, meta_line1, size=20, ink_level=0)

    status_up = status.upper()
    if status_up == "OFFLINE":
        prefix_str = "STATUS: "
        suffix_str = f"  •  LOG: {n_samples}  •  SYNC: {sync_str}"
        w_prefix = measure_text(prefix_str, size=19)
        w_status = measure_text(status_up, size=19)
        w_suffix = measure_text(suffix_str, size=19)
        pad_x = 5
        x_cur = 1164 - (w_prefix + w_status + pad_x * 2 + w_suffix)
        draw_text(display, x_cur, 71, prefix_str, size=19, ink_level=0)
        x_cur += w_prefix + pad_x
        draw_reversed_text(display, x_cur, 71, status_up, size=19, pad_x=pad_x, pad_y=3)
        x_cur += w_status + pad_x
        draw_text(display, x_cur, 71, suffix_str, size=19, ink_level=0)
    else:
        day_lbl = "DAY" if span_days == 1 else "DAYS"
        smp_lbl = "SAMPLE" if n_samples == 1 else "SAMPLES"
        meta_line2 = f"LOGGED: {span_days} {day_lbl} ({n_samples} {smp_lbl})  •  SYNC: {sync_str}"
        w2 = measure_text(meta_line2, size=19)
        draw_text(display, 1164 - w2, 71, meta_line2, size=19, ink_level=0)

    last_rec = records[-1]
    cur_day = int(last_rec.get("d", 0))
    cur_wday = int(last_rec.get("w", 3))
    wday_names_3 = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
    wday_names_1 = ["M", "T", "W", "T", "F", "S", "S"]

    # Filter 7-day window of records
    t_now = last_rec["t"]
    t_7d_start = t_now - 7 * 86400
    recs_7d = [r for r in records if r["t"] >= t_7d_start] or [last_rec]

    # 3. PANEL 01 // STATE OF CHARGE & RANGE TIMELINE (x=36, y=120, w=588, h=294)
    socs_7d = [float(r["soc"]) for r in recs_7d]
    cur_soc = int(round(socs_7d[-1]))
    cur_dte = int(last_rec.get("dte", 107))
    min_soc = int(round(min(socs_7d)))
    max_soc = int(round(max(socs_7d)))

    # Count charging sessions & kWh added across 7d log, and detect charge intervals
    n_charges = 0
    kwh_added = 0.0
    in_charge = False
    charge_windows = []
    chg_start_t = None
    chg_start_soc = None
    last_chg_t = None
    last_chg_soc = None

    for i in range(1, len(recs_7d)):
        prev_r = recs_7d[i - 1]
        curr_r = recs_7d[i]
        dsoc = float(curr_r["soc"]) - float(prev_r["soc"])
        is_charging = (curr_r.get("chg") == "charging_active" and dsoc >= 0.0) or (dsoc >= 1.5)
        if is_charging:
            if not in_charge and dsoc >= 1.5:
                n_charges += 1
                in_charge = True
            if dsoc > 0:
                kwh_added += (dsoc / 100.0) * float(curr_r.get("cap", 90.9))
            if chg_start_t is None:
                chg_start_t = prev_r["t"]
                chg_start_soc = float(prev_r["soc"])
            last_chg_t = curr_r["t"]
            last_chg_soc = float(curr_r["soc"])
        elif chg_start_t is not None and (dsoc <= -0.5 or (curr_r["t"] - last_chg_t) >= 3600):
            in_charge = False
            delta = (last_chg_soc - chg_start_soc) if last_chg_soc is not None else 0
            if delta >= 4.0:
                charge_windows.append((chg_start_t, last_chg_t, f"+{int(round(delta))}%"))
            chg_start_t = None
            chg_start_soc = None

    if chg_start_t is not None:
        delta = (last_chg_soc - chg_start_soc) if last_chg_soc is not None else 0
        if delta >= 4.0:
            charge_windows.append((chg_start_t, last_chg_t, f"+{int(round(delta))}%"))

    kpi1 = f"NOW: {cur_soc}% ({cur_dte} MI)  •  MIN/MAX: {min_soc}%/{max_soc}%  •  CHARGES: {n_charges} (+{int(round(kwh_added))} kWh)"
    draw_text(display, 50, 162, kpi1, size=17, ink_level=0)

    p1_x0, p1_x1, p1_y0, p1_y1 = 90, 566, 198, 380
    p1_w, p1_h = p1_x1 - p1_x0, p1_y1 - p1_y0

    # Map 7d points to (px, py)
    t_first = recs_7d[0]["t"]
    t_span = max(1, t_now - t_first)
    use_full_7d_scale = (t_span >= 86400)
    pts_p1 = []
    if len(recs_7d) == 1:
        gy = int(round(p1_y1 - (cur_soc / 100.0) * p1_h))
        pts_p1 = [(p1_x0, gy), (p1_x1, gy)]
    else:
        for r in recs_7d:
            frac = ((r["t"] - t_7d_start) / (7.0 * 86400.0)) if use_full_7d_scale else ((r["t"] - t_first) / float(t_span))
            frac = max(0.0, min(1.0, frac))
            px = int(round(p1_x0 + frac * p1_w))
            py = int(round(p1_y1 - (float(r["soc"]) / 100.0) * p1_h))
            pts_p1.append((px, py))

    # Fast byte-level area fill under SOC curve (GS4 level 6 = light gray)
    for idx in range(len(pts_p1) - 1):
        xa, ya = pts_p1[idx]
        xb, yb = pts_p1[idx + 1]
        if xb >= xa:
            dx_seg = max(1, xb - xa)
            for px in range(xa, xb + 1):
                y_top = max(p1_y0 + 1, ya + int((yb - ya) * (px - xa) / dx_seg))
                b_col = px >> 1
                if (px & 1) == 0:
                    for py in range(y_top, p1_y1):
                        bi = py * 600 + b_col
                        fb[bi] = (fb[bi] & 0xF0) | 6
                else:
                    for py in range(y_top, p1_y1):
                        bi = py * 600 + b_col
                        fb[bi] = (fb[bi] & 0x0F) | 0x60

    # Shaded Charging Window Columns (level 5 bar + subtle 2px level 3 parallel 45° stripes)
    chg_labels = []
    for t0, t1, chg_lbl in charge_windows:
        frac0 = ((t0 - t_7d_start) / (7.0 * 86400.0)) if use_full_7d_scale else ((t0 - t_first) / float(t_span))
        frac1 = ((t1 - t_7d_start) / (7.0 * 86400.0)) if use_full_7d_scale else ((t1 - t_first) / float(t_span))
        frac0 = max(0.0, min(1.0, frac0))
        frac1 = max(0.0, min(1.0, frac1))
        cx0 = int(round(p1_x0 + frac0 * p1_w))
        cx1 = int(round(p1_x0 + frac1 * p1_w))
        cx1 = max(cx0 + 5, cx1)

        # Slightly darker gray bar fill (level 5) from top to bottom of chart
        _fb_fill_rect(fb, cx0, p1_y0 + 1, cx1, p1_y1 - 1, 5)

        # Subtle tone-on-tone 2px parallel 45° diagonal stripes (level 3, 10px spacing)
        hx0, hx1 = cx0 + 1, cx1 - 1
        hy0, hy1 = p1_y0 + 2, p1_y1 - 2
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
                _fb_line(fb, sx0, sy0, sx1, sy1, c=3, w=2)

        # Queue callout badge inside the top of the charging column (e.g. +48%) for overlap-free placement
        tw = measure_text(chg_lbl, size=17)
        lx = max(p1_x0 + 3, min(p1_x1 - tw - 3, cx0 + (cx1 - cx0 - tw) // 2))
        chg_labels.append([lx, tw, chg_lbl])

    # Restore horizontal dashed grid lines over area fill (75%, 50%, 25%, 15% warn)
    for pct_tick in (75, 50, 25):
        gy = int(round(p1_y1 - (pct_tick / 100.0) * p1_h))
        _fb_dashed_line(fb, p1_x0, gy, p1_x1, gy, dash=4, gap=4, c=4, w=1)
    warn_y = int(round(p1_y1 - 0.15 * p1_h))
    _fb_dashed_line(fb, p1_x0, warn_y, p1_x1, warn_y, dash=8, gap=4, c=2, w=1)

    # Bold black SOC curve & terminal dot
    for idx in range(len(pts_p1) - 1):
        _fb_line(fb, pts_p1[idx][0], pts_p1[idx][1], pts_p1[idx + 1][0], pts_p1[idx + 1][1], c=0, w=3)
    _fb_circle(fb, pts_p1[-1][0], pts_p1[-1][1], r=4, fill_c=0, inner_c=7)

    # Spread overlapping charge callout labels cleanly apart (min 6px gap) and stamp inside top of chart
    min_gap = 6
    for idx in range(1, len(chg_labels)):
        prev_r = chg_labels[idx - 1][0] + chg_labels[idx - 1][1]
        if chg_labels[idx][0] < prev_r + min_gap:
            overlap = (prev_r + min_gap) - chg_labels[idx][0]
            shift_l = overlap // 2
            shift_r = overlap - shift_l
            new_prev = max(p1_x0 + 3, chg_labels[idx - 1][0] - shift_l)
            actual_l = chg_labels[idx - 1][0] - new_prev
            chg_labels[idx - 1][0] = new_prev
            chg_labels[idx][0] = min(p1_x1 - chg_labels[idx][1] - 3, chg_labels[idx][0] + shift_r + (shift_l - actual_l))
    ly_chg = p1_y0 + 7
    for lx, tw, chg_lbl in chg_labels:
        _fb_fill_rect(fb, lx - 2, ly_chg - 1, lx + tw + 2, ly_chg + 17, 7)
        draw_text(display, lx, ly_chg, chg_lbl, size=17, ink_level=0)

    # 7-Day X-Axis ticks & weekday labels ending on cur_wday
    for i in range(7):
        dx = int(round(p1_x0 + (i / 6.0) * p1_w))
        _fb_line(fb, dx, p1_y1, dx, p1_y1 + 5, c=0, w=2)
        d_lbl = wday_names_3[(cur_wday - 6 + i) % 7]
        dw = measure_text(d_lbl, size=17)
        draw_text(display, dx - dw // 2, p1_y1 + 8, d_lbl, size=17, ink_level=0)

    # 4. PANEL 02 // DAILY DISTANCE DRIVEN (x=638, y=120, w=526, h=294)
    # Group records by local day index for the last 14 days
    day_min_odo = {}
    day_max_odo = {}
    day_kwh_used = {}
    for idx, r in enumerate(records):
        d = int(r.get("d", cur_day))
        o = float(r.get("odo", 0.0))
        if d not in day_min_odo or o < day_min_odo[d]:
            day_min_odo[d] = o
        if d not in day_max_odo or o > day_max_odo[d]:
            day_max_odo[d] = o
        if idx > 0:
            prev_r = records[idx - 1]
            dsoc = float(prev_r["soc"]) - float(r["soc"])
            d_odo = o - float(prev_r.get("odo", o))
            if dsoc > 0 and d_odo >= 0:
                kwh_u = (dsoc / 100.0) * float(r.get("cap", 90.9))
                day_kwh_used[d] = day_kwh_used.get(d, 0.0) + kwh_u

    daily_miles_14 = []
    for i in range(14):
        target_d = cur_day - 13 + i
        if target_d in day_max_odo:
            # Prefer delta from the immediately preceding day's max odometer if logged
            if (target_d - 1) in day_max_odo:
                mi = max(0.0, day_max_odo[target_d] - day_max_odo[target_d - 1])
            else:
                mi = max(0.0, day_max_odo[target_d] - day_min_odo[target_d])
            daily_miles_14.append(int(round(mi)))
        else:
            daily_miles_14.append(0)

    tot_14d = sum(daily_miles_14)
    active_days_cnt = max(1, min(14, span_days))
    avg_mi = tot_14d / float(active_days_cnt)
    peak_mi = max(daily_miles_14) if daily_miles_14 else 0

    kpi2 = f"14D TOTAL: {tot_14d} MI   •   AVG: {avg_mi:.1f} MI/D   •   PEAK: {peak_mi} MI"
    draw_text(display, 652, 162, kpi2, size=17, ink_level=0)

    p2_x0, p2_x1, p2_y0, p2_y1 = 686, 1146, 200, 380
    p2_w, p2_h = p2_x1 - p2_x0, p2_y1 - p2_y0
    slot_w2 = p2_w / 14.0
    bar_w2 = int(slot_w2 * 0.64)
    max_y_mi = max(100.0, float(peak_mi + 15))

    for i in range(14):
        mi = daily_miles_14[i]
        cx = int(round(p2_x0 + (i + 0.5) * slot_w2))
        bx0 = cx - bar_w2 // 2
        bx1 = bx0 + bar_w2
        if mi > 0:
            bh = max(4, int(round((mi / max_y_mi) * p2_h)))
            by0 = max(p2_y0, p2_y1 - bh)
            is_peak = (mi == peak_mi and peak_mi > 0)
            is_today = (i == 13)
            fill_c = 0 if (is_peak or is_today) else 3
            _fb_rect_box(fb, bx0, by0, bx1, p2_y1, fill_c=fill_c, outline_c=0, bw=2)
            if is_peak:
                p_str = str(mi)
                pw = measure_text(p_str, size=17)
                draw_text(display, cx - pw // 2, by0 - 20, p_str, size=17, ink_level=0)
        d_ch = wday_names_1[(cur_wday - 13 + i) % 7]
        dw = measure_text(d_ch, size=17)
        draw_text(display, cx - dw // 2, p2_y1 + 8, d_ch, size=17, ink_level=0)

    avg_y = int(round(p2_y1 - (min(max_y_mi, avg_mi) / max_y_mi) * p2_h))
    _fb_dashed_line(fb, p2_x0, avg_y, p2_x1, avg_y, dash=7, gap=4, c=0, w=2)
    avg_tag = f"AVG {int(round(avg_mi))} MI"
    tw_avg = measure_text(avg_tag, size=17)
    tag_x = p2_x0 + 8
    tag_y = max(p2_y0 + 2, avg_y - 22)
    _fb_rect_box(fb, tag_x, tag_y, tag_x + tw_avg + 8, tag_y + 18, fill_c=7, outline_c=0, bw=1)
    draw_text(display, tag_x + 4, tag_y + 2, avg_tag, size=17, ink_level=0)

    # 5. PANEL 03 // 3-ZONE THERMAL (x=36, y=428, w=368, h=296)
    cur_tp = int(last_rec.get("tp", 68))
    cur_tc = int(last_rec.get("tc", 70))
    cur_te = int(last_rec.get("te", 60))
    draw_text(display, 76, 470, f"PACK {cur_tp}°", size=17, ink_level=0)
    draw_text(display, 196, 470, f"CABIN {cur_tc}°", size=17, ink_level=0)
    draw_text(display, 320, 470, f"EXT {cur_te}°", size=17, ink_level=2)

    p3_x0, p3_x1, p3_y0, p3_y1 = 82, 378, 508, 690
    p3_w, p3_h = p3_x1 - p3_x0, p3_y1 - p3_y0

    def _temp_y(tf: float) -> int:
        clamped = max(40.0, min(90.0, float(tf)))
        return int(round(p3_y1 - ((clamped - 40.0) / 50.0) * p3_h))

    if len(recs_7d) == 1:
        ext_pts = [(p3_x0, _temp_y(cur_te)), (p3_x1, _temp_y(cur_te))]
        cab_pts = [(p3_x0, _temp_y(cur_tc)), (p3_x1, _temp_y(cur_tc))]
        pack_pts = [(p3_x0, _temp_y(cur_tp)), (p3_x1, _temp_y(cur_tp))]
    else:
        step3 = max(1, len(recs_7d) // 24)
        s3 = recs_7d[::step3]
        if s3[-1] is not recs_7d[-1]:
            s3.append(recs_7d[-1])
        ext_pts, cab_pts, pack_pts = [], [], []
        for r in s3:
            frac = ((r["t"] - t_7d_start) / (7.0 * 86400.0)) if use_full_7d_scale else ((r["t"] - t_first) / float(t_span))
            frac = max(0.0, min(1.0, frac))
            px = int(round(p3_x0 + frac * p3_w))
            ext_pts.append((px, _temp_y(r.get("te", 60))))
            cab_pts.append((px, _temp_y(r.get("tc", 70))))
            pack_pts.append((px, _temp_y(r.get("tp", 68))))

    for i in range(len(ext_pts) - 1):
        _fb_line(fb, ext_pts[i][0], ext_pts[i][1], ext_pts[i + 1][0], ext_pts[i + 1][1], c=3, w=2)
    for i in range(len(cab_pts) - 1):
        _fb_dashed_line(fb, cab_pts[i][0], cab_pts[i][1], cab_pts[i + 1][0], cab_pts[i + 1][1], dash=5, gap=3, c=0, w=2)
    for i in range(len(pack_pts) - 1):
        _fb_line(fb, pack_pts[i][0], pack_pts[i][1], pack_pts[i + 1][0], pack_pts[i + 1][1], c=0, w=3)
    _fb_circle(fb, pack_pts[-1][0], pack_pts[-1][1], r=4, fill_c=0)

    for i in range(7):
        tx = int(round(p3_x0 + (i / 6.0) * p3_w))
        _fb_line(fb, tx, p3_y1, tx, p3_y1 + 5, c=0, w=2)
        d_lbl = wday_names_3[(cur_wday - 6 + i) % 7]
        dw = measure_text(d_lbl, size=17)
        draw_text(display, tx - dw // 2, p3_y1 + 8, d_lbl, size=17, ink_level=0)

    # 6. PANEL 04 // EFFICIENCY & CAPACITY (x=416, y=428, w=368, h=296)
    p4_x0, p4_x1, p4_y0, p4_y1 = 462, 766, 476, 602
    p4_w, p4_h = p4_x1 - p4_x0, p4_y1 - p4_y0
    slot_w4 = p4_w / 7.0
    r_y4 = int(round(p4_y1 - ((3.0 - 1.5) / 3.0) * p4_h))

    # Derive current implied efficiency from DTE / (SOC * Cap) as fallback when driving log is still accumulating
    cur_cap = float(last_rec.get("cap", 90.9))
    rem_kwh = max(1.0, (float(last_rec.get("soc", 32.0)) / 100.0) * cur_cap)
    implied_eff = max(1.8, min(4.2, float(cur_dte) / rem_kwh))

    tot_kwh_14 = sum(day_kwh_used.values())
    avg_eff = (tot_14d / tot_kwh_14) if (tot_kwh_14 >= 1.0 and tot_14d >= 2) else implied_eff
    avg_eff = max(1.5, min(4.5, avg_eff))

    eff_pts = []
    for i in range(7):
        target_d = cur_day - 6 + i
        cx = int(round(p4_x0 + (i + 0.5) * slot_w4))
        _fb_line(fb, cx, p4_y1, cx, p4_y1 + 4, c=0, w=2)
        d_lbl = wday_names_3[(cur_wday - 6 + i) % 7]
        dw = measure_text(d_lbl, size=17)
        draw_text(display, cx - dw // 2, p4_y1 + 6, d_lbl, size=17, ink_level=0)

        mi_d = daily_miles_14[7 + i]
        kwh_d = day_kwh_used.get(target_d, 0.0)
        if mi_d >= 1 and kwh_d >= 0.3:
            e_val = max(1.5, min(4.5, mi_d / kwh_d))
        elif i == 6:
            e_val = avg_eff
        else:
            e_val = None

        if e_val is not None:
            cy = int(round(p4_y1 - ((e_val - 1.5) / 3.0) * p4_h))
            _fb_line(fb, cx, r_y4, cx, cy, c=2, w=2)
            eff_pts.append((cx, cy, e_val))

    for i in range(len(eff_pts) - 1):
        _fb_line(fb, eff_pts[i][0], eff_pts[i][1], eff_pts[i + 1][0], eff_pts[i + 1][1], c=0, w=2)
    for cx, cy, e_val in eff_pts:
        if e_val >= 3.0:
            _fb_circle(fb, cx, cy, r=5, fill_c=0)
        else:
            _fb_circle(fb, cx, cy, r=5, fill_c=0, inner_c=7)

    draw_text(display, 430, 668, f"{avg_eff:.2f} MI/kWh", size=22, ink_level=0)
    draw_text(display, 430, 698, f"USED: {int(round(tot_kwh_14))} kWh (14D)", size=17, ink_level=0)
    draw_text(display, 628, 668, f"{cur_cap:.1f} kWh", size=22, ink_level=0)

    # 7. PANEL 05 // TIRE PRESSURE (x=796, y=428, w=368, h=170)
    sc_x0, sc_x1 = 860, 1088
    sc_w = sc_x1 - sc_x0
    t_top = 493
    r_h = 25

    def _psi_x(psi: float) -> int:
        clamped = max(30.0, min(44.0, float(psi)))
        return int(round(sc_x0 + ((clamped - 30.0) / 14.0) * sc_w))

    for c_idx in range(4):
        vals = [float(r["psi"][c_idx]) for r in records if isinstance(r.get("psi"), list) and len(r["psi"]) == 4]
        p_now = vals[-1] if vals else 38.0
        p_min = min(vals) if vals else p_now
        p_max = max(vals) if vals else p_now
        ry = t_top + c_idx * r_h
        cy = ry + 10
        rx0 = _psi_x(p_min)
        rx1 = _psi_x(p_max)
        if rx1 - rx0 >= 2:
            _fb_rect_box(fb, rx0, cy - 4, rx1, cy + 4, fill_c=4, outline_c=0, bw=1)
            _fb_line(fb, rx0, cy - 6, rx0, cy + 6, c=0, w=2)
            _fb_line(fb, rx1, cy - 6, rx1, cy + 6, c=0, w=2)
        nx = _psi_x(p_now)
        _fb_circle(fb, nx, cy, r=4, fill_c=0)
        v_str = f"{p_now:.1f}"
        vw = measure_text(v_str, size=19)
        draw_text(display, 1150 - vw, ry + 1, v_str, size=19, ink_level=0)

    # 8. PANEL 06 // INKPLATE STATUS (x=796, y=612, w=368, h=112)
    ink_v, ink_pct = _get_inkplate_battery(display)
    drain_per_day = 1.4
    if len(records) >= 4:
        dt_days = (records[-1]["t"] - records[0]["t"]) / 86400.0
        dpct = float(records[0].get("ip", ink_pct)) - float(records[-1].get("ip", ink_pct))
        if dt_days >= 0.25 and dpct > 0.1:
            drain_per_day = max(0.3, min(15.0, dpct / dt_days))
    days_to_charge = int(round(ink_pct / drain_per_day)) if drain_per_day > 0 else 60

    draw_text(display, 810, 656, f"{ink_pct}%", size=26, ink_level=0)
    draw_text(display, 892, 659, f"{days_to_charge} DAYS TO CHARGE", size=20, ink_level=0)
    draw_text(display, 810, 694, f"DAILY DRAIN: -{drain_per_day:.1f}% / DAY", size=18, ink_level=0)

    # 9. Footer Inkplate Battery Icon Fill (X=1128, Y=748)
    ib_x, ib_y, ib_inner_w, ib_inner_h = 1128 + 3, 740 + 8 + 3, 26, 10
    ib_fill_w = max(0, min(ib_inner_w, int(round(ib_inner_w * (ink_pct / 100.0)))))
    if ib_fill_w > 0:
        _fb_fill_rect(fb, ib_x, ib_y, ib_x + ib_fill_w - 1, ib_y + ib_inner_h - 1, 0)

    if flush:
        display.display()


def render_active_page(
    display, telemetry: dict, sync_str: str, status: str = "ONLINE", page: int = 1, flush: bool = True
) -> None:
    if int(page) == 2:
        render_page2_on_inkplate(display, telemetry, sync_str, status=status, flush=flush)
    else:
        render_dashboard_on_inkplate(display, telemetry, sync_str, status=status, flush=flush)


def _fallback_telemetry(config: dict) -> dict:
    cached = config.get("last_telemetry")
    if isinstance(cached, dict) and cached:
        return cached
    return {
        "batteryLevel": 47.0,
        "batteryCapacity": 90.9,
        "distanceToEmpty": 254.0,
        "vehicleMileage": 2977286.0,
        "cabinClimateInteriorTemperature": 21.0,
        "cabinClimateExteriorTemperature": 18.0,
        "batteryTemperature": 21.0,
        "battery12VState": "NORMAL",
        "chargerState": "charging_ready",
        "tirePressureFrontLeft": 38,
        "tirePressureFrontRight": 38,
        "tirePressureRearLeft": 38,
        "tirePressureRearRight": 38,
    }


def _get_active_page(config: dict = None) -> int:
    try:
        with open("/page.txt", "r") as f:
            return 2 if f.read().strip() == "2" else 1
    except Exception:
        if config is not None:
            return 2 if int(config.get("active_page", 1)) == 2 else 1
        return 1


def _set_active_page(p: int) -> None:
    try:
        with open("/page.txt", "w") as f:
            f.write("2" if int(p) == 2 else "1")
    except Exception:
        pass


def run(display=None) -> None:
    import machine
    from inkplate10 import Inkplate

    if display is None:
        mode = getattr(Inkplate, "INKPLATE_2BIT", getattr(Inkplate, "INKPLATE_3BIT", 1))
        display = Inkplate(mode)
        display.begin()

    for p in ("/", "/assets", "/sd", "/sd/assets"):
        if p not in sys.path:
            sys.path.append(p)

    config = load_sd_config()
    cur_page = _get_active_page(config)

    wake_reason = 0
    try:
        wake_reason = machine.wake_reason()
    except Exception:
        pass

    wake_pin = None
    _btn_latched = False
    _last_irq_ms = time.ticks_ms()

    def _on_wake_irq(pin):
        nonlocal _btn_latched, _last_irq_ms
        now = time.ticks_ms()
        if time.ticks_diff(now, _last_irq_ms) > 180:
            _btn_latched = True
            _last_irq_ms = now

    try:
        wake_pin = machine.Pin(36, machine.Pin.IN)
    except Exception as e:
        print("Pin 36 init note:", e)

    fb_cache = {}
    active_telem = _fallback_telemetry(config)
    active_sync = str(config.get("last_sync_time_str") or "RECENT")
    active_status = "ONLINE"

    if wake_reason == 2:
        cur_page = 2 if cur_page == 1 else 1
        _set_active_page(cur_page)
        stay_awake_ms = 60000
    else:
        _set_active_page(cur_page)
        stay_awake_ms = 15000
        if connect_wifi(config):
            active_sync = sync_ntp_time(int(config.get("timezone_offset_hours", -5)), config=config)
            try:
                active_telem = fetch_all_r2_telemetry(config)
                config["last_telemetry"] = active_telem
                save_sd_config_atomic(config)
                append_telemetry_log(active_telem, config, display=display)
                gc.collect()
                active_status = "ONLINE"
            except Exception as e:
                print("Telemetry error, rendering OFFLINE state:", e)
                active_sync = format_offline_sync_str(config)
                active_status = "OFFLINE"
        else:
            print("Wi-Fi connection failed, rendering OFFLINE state.")
            active_sync = format_offline_sync_str(config)
            active_status = "OFFLINE"

    # Render and cache BOTH pages with the exact same active_status and active_sync
    import rle_cache

    for old_f in ("/cache_p1.bin", "/cache_p2.bin", "/cache_next.bin"):
        try:
            os.remove(old_f)
        except OSError:
            pass

    # 1. Render Page 1 and save compressed cache (~100 KB)
    render_active_page(display, active_telem, active_sync, status=active_status, page=1, flush=False)
    fb_cache[1] = bytes(display._framebuf())
    rle_cache.save_page_cache(1, display._framebuf())
    gc.collect()

    # 2. Render Page 2 and save compressed cache (~108 KB)
    render_active_page(display, active_telem, active_sync, status=active_status, page=2, flush=False)
    fb_cache[2] = bytes(display._framebuf())
    rle_cache.save_page_cache(2, display._framebuf())
    gc.collect()

    # 3. Load active page into display framebuffer
    memoryview(display._framebuf())[:] = fb_cache[cur_page]

    if wake_pin is not None:
        t_rel = time.ticks_ms()
        while wake_pin.value() == 0 and time.ticks_diff(time.ticks_ms(), t_rel) < 800:
            time.sleep_ms(10)
        time.sleep_ms(25)
        _btn_latched = False
        _last_irq_ms = time.ticks_ms()
        wake_pin.irq(trigger=machine.Pin.IRQ_FALLING, handler=_on_wake_irq)

    display.display()
    t_wait = time.ticks_ms()

    def _check_and_flip() -> bool:
        nonlocal cur_page, stay_awake_ms, t_wait, _btn_latched, _last_irq_ms
        if wake_pin is not None and (_btn_latched or wake_pin.value() == 0):
            _btn_latched = False
            cur_page = 2 if cur_page == 1 else 1
            _set_active_page(cur_page)
            print(f"GPIO 36 button pressed! Instant flip to Page {cur_page}...")
            memoryview(display._framebuf())[:] = fb_cache[cur_page]
            t_rel = time.ticks_ms()
            while wake_pin.value() == 0 and time.ticks_diff(time.ticks_ms(), t_rel) < 800:
                time.sleep_ms(10)
            time.sleep_ms(25)
            _btn_latched = False
            _last_irq_ms = time.ticks_ms()
            display.display()
            stay_awake_ms = 60000
            t_wait = time.ticks_ms()
            return True
        return False

    sleep_ms = compute_sleep_ms(config)
    print(f"BOTH PAGES CACHED ({active_status}): Interactive window ({stay_awake_ms // 1000}s) before {sleep_ms // 60000}m deep sleep...")

    while time.ticks_diff(time.ticks_ms(), t_wait) < stay_awake_ms:
        _check_and_flip()
        time.sleep_ms(20)

    fb_cache.clear()
    gc.collect()
    try:
        if wake_pin is not None:
            wake_pin.irq(handler=None)
            import esp32
            esp32.wake_on_ext0(pin=wake_pin, level=0)
    except Exception as e:
        print("ext0 setup note:", e)
    machine.deepsleep(sleep_ms)
