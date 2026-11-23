#!/usr/bin/env python3
"""
Rivian R2 Live Art Frame - Live Telemetry Fetcher
1. Authenticates via Rivian's GraphQL gateway using refresh_token from config.json
   and saves any rotated refresh_token / access_token back to config.json.
2. Queries registered vehicles to extract the R2 Vehicle ID and fetches
   actualGeneralAssemblyDate ("Born On" date) once, caching it in config.json.
3. Fetches vehicle telemetry from both HTTP GetVehicleState and Rivian's
   WebSocket Parallax stream (wss://api.rivian.com/gql-consumer-subscriptions/graphql)
   to retrieve:
     - tirePressureFrontLeft, tirePressureFrontRight, tirePressureRearLeft, tirePressureRearRight
     - batteryLevel, chargerState, distanceToEmpty, vehicleMileage
     - battery12VState, cabinClimateInteriorTemperature
4. Converts cabin temperature to Fahrenheit (and bar -> PSI, km/m -> miles)
   and prints all parsed variables clearly to the terminal.
"""

import warnings

# Suppress macOS LibreSSL 2.8.3 NotOpenSSLWarning emitted by urllib3 v2 before importing requests
warnings.filterwarnings("ignore")

import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
import requests

URL = "https://rivian.com/api/gql/gateway/graphql"
WS_HOST = "api.rivian.com"
WS_PATH = "/gql-consumer-subscriptions/graphql"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(REPO_ROOT, "config.json")

BASE_HEADERS = {
    "User-Agent": "RivianApp/707 CFNetwork/1237 Darwin/20.4.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": "com.rivian.ios.consumer-apollo-ios",
}

BAR_TO_PSI = 14.5037738
KM_TO_MILES = 0.621371
METERS_TO_MILES = 0.000621371


def load_config(path: str = CONFIG_PATH) -> Dict[str, Any]:
    """Load credentials and cached metadata from config.json."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Configuration file not found at '{path}'. Run get_token.py first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(config: Dict[str, Any], path: str = CONFIG_PATH) -> None:
    """Persist updated tokens and cached vehicle metadata back to config.json."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def create_csrf_tokens(session: requests.Session) -> Tuple[str, str]:
    """Fetch fresh CSRF and App Session tokens if not already present."""
    payload = {
        "operationName": "CreateCSRFToken",
        "query": (
            "mutation CreateCSRFToken {\n"
            "  createCsrfToken {\n"
            "    __typename\n"
            "    csrfToken\n"
            "    appSessionToken\n"
            "  }\n"
            "}"
        ),
        "variables": None,
    }
    resp = session.post(URL, json=payload, headers=BASE_HEADERS, timeout=15)
    resp.raise_for_status()
    data = (resp.json().get("data") or {}).get("createCsrfToken") or {}
    return data.get("csrfToken", ""), data.get("appSessionToken", "")


def authenticate_with_refresh_token(
    session: requests.Session, config: Dict[str, Any], config_path: str = CONFIG_PATH
) -> Dict[str, str]:
    """
    Exchange the refresh_token in config.json for a short-lived accessToken
    via Rivian's GraphQL gateway and save any newly returned refreshToken
    back to config.json.
    """
    refresh_token = config.get("refresh_token")
    if not refresh_token:
        raise ValueError("Missing 'refresh_token' in config.json.")

    csrf_token = config.get("csrf_token")
    app_session_token = config.get("app_session_token")
    if not csrf_token or not app_session_token:
        csrf_token, app_session_token = create_csrf_tokens(session)
        config["csrf_token"] = csrf_token
        config["app_session_token"] = app_session_token

    headers = BASE_HEADERS.copy()
    headers["Csrf-Token"] = csrf_token
    headers["A-Sess"] = app_session_token
    if config.get("user_session_token"):
        headers["U-Sess"] = config["user_session_token"]

    mutation = """mutation RefreshAccessToken($refreshToken: String!) {
  refreshAccessToken(refreshToken: $refreshToken) {
    __typename
    accessToken
    refreshToken
  }
}"""
    payload = {
        "operationName": "RefreshAccessToken",
        "query": mutation,
        "variables": {"refreshToken": refresh_token},
    }

    resp = session.post(URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    if "errors" in body:
        err_msg = body["errors"][0].get("message", "Unknown authentication error")
        raise RuntimeError(f"Token refresh failed: {err_msg}")

    token_data = (body.get("data") or {}).get("refreshAccessToken") or {}
    new_access_token = token_data.get("accessToken")
    new_refresh_token = token_data.get("refreshToken")

    if not new_access_token:
        raise RuntimeError(f"No accessToken returned by gateway: {body}")

    config["access_token"] = new_access_token
    if new_refresh_token:
        config["refresh_token"] = new_refresh_token

    save_config(config, config_path)
    headers["Authorization"] = f"Bearer {new_access_token}"
    return headers


def accept_vehicle_invite(
    session: requests.Session, headers: Dict[str, str], invite_id: str
) -> bool:
    """Accept a pending vehicle invitation so telemetry access is granted."""
    mutation = """mutation AcceptVehicleInvite($inviteId: String!) {
  acceptVehicleInvite(inviteId: $inviteId) {
    __typename
    success
  }
}"""
    payload = {
        "operationName": "AcceptVehicleInvite",
        "query": mutation,
        "variables": {"inviteId": invite_id},
    }
    resp = session.post(URL, json=payload, headers=headers, timeout=15)
    if resp.status_code == 200:
        data = (resp.json().get("data") or {}).get("acceptVehicleInvite") or {}
        return bool(data.get("success"))
    return False


def get_r2_vehicle_and_born_on_date(
    session: requests.Session,
    headers: Dict[str, str],
    config: Dict[str, Any],
    config_path: str = CONFIG_PATH,
) -> Tuple[str, str, Optional[str]]:
    """
    Query the user's registered vehicles to extract the string Vehicle ID for the R2.
    Fetches actualGeneralAssemblyDate ("Born On:" date) only if not already cached
    in config.json, then persists it to config.json.
    Returns (vehicle_id, vehicle_label, born_on_date).
    """
    cached_born_on = config.get("actual_general_assembly_date")

    # Only include actualGeneralAssemblyDate in the query if we haven't cached it yet
    assembly_field = "" if cached_born_on else "actualGeneralAssemblyDate"

    query = f"""query getUserInfo {{
  currentUser {{
    __typename
    id
    vehicles {{
      __typename
      id
      name
      vin
      state
      roles
      vehicle {{
        __typename
        id
        vin
        make
        model
        modelYear
        {assembly_field}
      }}
    }}
    pendingInvites {{
      __typename
      id
      vehicleId
      vehicleModel
      status
    }}
  }}
}}"""
    payload = {
        "operationName": "getUserInfo",
        "query": query,
        "variables": None,
    }

    resp = session.post(URL, json=payload, headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    if "errors" in body:
        err_msg = body["errors"][0].get("message", "Unknown error")
        raise RuntimeError(f"getUserInfo query failed: {err_msg}")

    current_user = (body.get("data") or {}).get("currentUser") or {}
    vehicles = current_user.get("vehicles") or []
    pending_invites = current_user.get("pendingInvites") or []

    # 1. Search active registered vehicles for an R2 model or name match
    for v in vehicles:
        v_id = v.get("id")
        v_name = v.get("name") or ""
        v_info = v.get("vehicle") or {}
        v_model = v_info.get("model") or ""
        v_vin = v.get("vin") or v_info.get("vin") or ""
        if v_id and ("R2" in v_model.upper() or "R2" in v_name.upper()):
            born_on = cached_born_on or v_info.get("actualGeneralAssemblyDate")
            config_updated = False
            if born_on and config.get("actual_general_assembly_date") != born_on:
                config["actual_general_assembly_date"] = born_on
                config_updated = True
            if v_name and config.get("vehicle_name") != v_name:
                config["vehicle_name"] = v_name
                config_updated = True
            if config.get("vehicle_id") != str(v_id):
                config["vehicle_id"] = str(v_id)
                config_updated = True
            if config_updated:
                save_config(config, config_path)
            label = f"Rivian {v_model} '{v_name}' (VIN: {v_vin})" if v_name else f"Rivian {v_model} (VIN: {v_vin})"
            return str(v_id), label, born_on

    # 2. If there is a pending R2 invite, auto-accept it so telemetry is unlocked
    for inv in pending_invites:
        inv_id = inv.get("id")
        v_id = inv.get("vehicleId")
        v_model = inv.get("vehicleModel") or ""
        if v_id and "R2" in v_model.upper():
            if inv_id and inv.get("status") == "PENDING":
                accept_vehicle_invite(session, headers, inv_id)
            return str(v_id), f"Rivian {v_model}", cached_born_on

    # 3. Fallback to first available registered vehicle
    if vehicles and vehicles[0].get("id"):
        first = vehicles[0]
        v_info = first.get("vehicle") or {}
        model = v_info.get("model") or first.get("name") or "Unknown"
        born_on = cached_born_on or v_info.get("actualGeneralAssemblyDate")
        if born_on and not cached_born_on:
            config["actual_general_assembly_date"] = born_on
            save_config(config, config_path)
        return str(first["id"]), f"Rivian {model}", born_on

    raise RuntimeError("No registered vehicles found on this account.")


# ==============================================================================
# PURE-PYTHON PROTOBUF & PARALLAX WEBSOCKET DECODER
# ==============================================================================

def _parse_proto_fields(data: bytes) -> List[Tuple[int, int, Any]]:
    """Minimal pure-Python Protobuf wire format parser (wire types 0, 1, 2, 5)."""
    i = 0
    n = len(data)
    fields: List[Tuple[int, int, Any]] = []
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
        if wire_type == 0:  # Varint
            val = 0
            shift = 0
            while True:
                b = data[i]
                i += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
        elif wire_type == 1:  # 64-bit
            val = data[i : i + 8]
            i += 8
        elif wire_type == 2:  # Length-delimited
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
        elif wire_type == 5:  # 32-bit
            val = data[i : i + 4]
            i += 4
        else:
            break
        fields.append((field_num, wire_type, val))
    return fields


def decode_parallax_rvm(rvm: str, b64_payload: str) -> Dict[str, Any]:
    """Decode base64-encoded Parallax Protobuf RVM payloads into telemetry keys."""
    if not b64_payload:
        return {}
    raw = base64.b64decode(b64_payload)
    out: Dict[str, Any] = {}

    if rvm == "dynamics.tires.state":
        # message TiresState { int32 tpms_monitor_status = 1; repeated Tire tire = 2; }
        # message Tire { TirePosition pos = 1; optional TirePressureStatus status = 2; optional double pressure = 3 (bar); int32 validity = 4; }
        pos_map = {
            1: "tirePressureFrontLeft",
            2: "tirePressureFrontRight",
            3: "tirePressureRearLeft",
            4: "tirePressureRearRight",
        }
        status_map = {
            1: "OK",
            2: "WARNING_HARD",
            3: "WARNING_SOFT",
            4: "WARNING_PUNCTURE",
        }
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 2 and wtype == 2:
                tire_fields = {tf: (tw, tv) for tf, tw, tv in _parse_proto_fields(val)}
                pos = tire_fields.get(1, (0, 0))[1]
                key = pos_map.get(pos)
                if not key:
                    continue
                if 3 in tire_fields and len(tire_fields[3][1]) == 8:
                    bar_val = struct.unpack("<d", tire_fields[3][1])[0]
                    psi_val = round(bar_val * BAR_TO_PSI, 1)
                    out[key] = psi_val
                elif 2 in tire_fields:
                    out[key] = status_map.get(tire_fields[2][1], "UNKNOWN")

    elif rvm == "comfort.cabin.cabin_temperatures":
        # message CabinTemperatures { repeated ZoneTemperature temperatures = 1; optional float exterior_temperature = 2; float interior_temperature = 3; optional float driver_set_point = 4; }
        for fnum, wtype, val in _parse_proto_fields(raw):
            if wtype == 5 and len(val) == 4:
                temp_c = round(struct.unpack("<f", val)[0], 1)
                if fnum == 2:
                    out["cabinClimateExteriorTemperature"] = temp_c
                elif fnum == 3:
                    out["cabinClimateInteriorTemperature"] = temp_c

    elif rvm == "energy.low_voltage.battery_state":
        # message LowVoltageBatteryState { optional LowVoltageHealth health = 1; } (1 = NORMAL, 2 = LOW)
        health_map = {1: "NORMAL", 2: "LOW"}
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["battery12VState"] = health_map.get(val, f"UNKNOWN ({val})")

    elif rvm == "energy.high_voltage.battery_state":
        # message BatteryState { ChargeState charge_state = 1; Temperatures temperatures = 2; ... }
        # ChargeState { double soc = 1; double pack_energy = 2; }
        # Temperatures { float mid = 1; float max = 2; float min = 3; }
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 2:
                for cfnum, cwtype, cval in _parse_proto_fields(val):
                    if cfnum == 1 and cwtype == 1 and len(cval) == 8:
                        soc = struct.unpack("<d", cval)[0]
                        out["batteryLevel"] = round(soc, 1)
            elif fnum == 2 and wtype == 2:
                for tfnum, twtype, tval in _parse_proto_fields(val):
                    if twtype == 5 and len(tval) == 4:
                        temp_c = round(struct.unpack("<f", tval)[0], 1)
                        if tfnum == 1:
                            out["batteryTemperature"] = temp_c
                        elif tfnum == 2:
                            out["batteryTempMax"] = temp_c
                        elif tfnum == 3:
                            out["batteryTempMin"] = temp_c

    elif rvm == "dynamics.vehicle.range":
        # message Range { int32 distance_to_empty = 1; } (km)
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["distanceToEmpty"] = val

    elif rvm == "dynamics.vehicle.odometer":
        # message Odometer { optional int64 distance = 1; } (km -> meters)
        for fnum, wtype, val in _parse_proto_fields(raw):
            if fnum == 1 and wtype == 0:
                out["vehicleMileage"] = val * 1000

    return out


def fetch_parallax_websocket_telemetry(
    user_session_token: str, vehicle_id: str, timeout_sec: float = 4.0
) -> Dict[str, Any]:
    """
    Connect to wss://api.rivian.com/gql-consumer-subscriptions/graphql using
    Python's standard library ssl/socket and subscribe to ParallaxMessages
    to retrieve tire pressures (PSI), interior cabin temperature (°C), and 12V battery state.
    """
    target_rvms = [
        "dynamics.tires.state",
        "comfort.cabin.cabin_temperatures",
        "energy.low_voltage.battery_state",
        "energy.high_voltage.battery_state",
        "dynamics.vehicle.range",
        "dynamics.vehicle.odometer",
    ]
    collected: Dict[str, Any] = {}
    seen_rvms = set()

    ctx = ssl.create_default_context()
    raw_sock = socket.create_connection((WS_HOST, 443), timeout=10)
    sock = ctx.wrap_socket(raw_sock, server_hostname=WS_HOST)

    try:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {WS_HOST}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: graphql-transport-ws\r\n"
            f"User-Agent: {BASE_HEADERS['User-Agent']}\r\n"
            "\r\n"
        )
        sock.sendall(handshake.encode("utf-8"))

        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket handshake closed unexpectedly.")
            resp += chunk
        _, leftover = resp.split(b"\r\n\r\n", 1)
        buf = bytearray(leftover)

        def ws_send(msg_dict: Dict[str, Any]) -> None:
            data = json.dumps(msg_dict).encode("utf-8")
            mask_key = os.urandom(4)
            length = len(data)
            header = bytearray([0x81])
            if length < 126:
                header.append(0x80 | length)
            elif length < 65536:
                header.append(0x80 | 126)
                header.extend(struct.pack("!H", length))
            else:
                header.append(0x80 | 127)
                header.extend(struct.pack("!Q", length))
            header.extend(mask_key)
            masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(data))
            sock.sendall(bytes(header) + masked)

        def recv_exact(n: int) -> bytes:
            while len(buf) < n:
                chunk = sock.recv(4096)
                if not chunk:
                    raise EOFError()
                buf.extend(chunk)
            out = bytes(buf[:n])
            del buf[:n]
            return out

        def ws_recv() -> Tuple[int, bytes]:
            b1, b2 = recv_exact(2)
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
                payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
            return opcode, payload

        # 1. Initialize graphql-transport-ws session
        ws_send(
            {
                "type": "connection_init",
                "payload": {
                    "client-name": BASE_HEADERS["Apollographql-Client-Name"],
                    "client-version": "1.13.0-1494",
                    "dc-cid": f"m-ios-{uuid.uuid4()}",
                    "u-sess": user_session_token,
                },
            }
        )

        opcode, ack_bytes = ws_recv()
        ack_data = json.loads(ack_bytes.decode("utf-8", errors="replace"))
        if ack_data.get("type") != "connection_ack":
            raise RuntimeError(f"WebSocket connection not acknowledged: {ack_data}")

        # 2. Subscribe to ParallaxMessages for the R2
        sub_id = str(uuid.uuid4())
        ws_send(
            {
                "id": sub_id,
                "type": "subscribe",
                "payload": {
                    "operationName": "ParallaxMessages",
                    "query": (
                        "subscription ParallaxMessages($vehicleId: String!, $rvms: [String!]) {\n"
                        "  parallaxMessages(vehicleId: $vehicleId, rvms: $rvms) {\n"
                        "    payload\n"
                        "    timestamp\n"
                        "    rvm\n"
                        "  }\n"
                        "}"
                    ),
                    "variables": {"vehicleId": vehicle_id, "rvms": target_rvms},
                },
            }
        )

        sock.settimeout(timeout_sec)
        deadline = time.time() + timeout_sec
        while time.time() < deadline and len(seen_rvms) < len(target_rvms):
            try:
                op, frame = ws_recv()
                if op != 1:
                    continue
                msg = json.loads(frame.decode("utf-8", errors="replace"))
                if msg.get("type") == "next":
                    px = ((msg.get("payload") or {}).get("data") or {}).get(
                        "parallaxMessages"
                    ) or {}
                    rvm = px.get("rvm")
                    b64_payload = px.get("payload")
                    if rvm and b64_payload is not None:
                        seen_rvms.add(rvm)
                        decoded = decode_parallax_rvm(rvm, b64_payload)
                        collected.update(decoded)
            except (socket.timeout, EOFError):
                break

        # Cleanly close subscription
        try:
            ws_send({"id": sub_id, "type": "complete"})
        except Exception:
            pass
    finally:
        sock.close()

    return collected


def fetch_vehicle_telemetry(
    session: requests.Session,
    headers: Dict[str, str],
    vehicle_id: str,
    user_session_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Execute the HTTP GetVehicleState GraphQL query AND merge in the missing
    Parallax RVM telemetry fields (tire pressures, 12V battery state, cabin temp).
    """
    primary_query = """query GetVehicleState($vehicleID: String!) {
  vehicleState(id: $vehicleID) {
    __typename
    tirePressureFrontLeft { timeStamp value }
    tirePressureFrontRight { timeStamp value }
    tirePressureStatusFrontLeft { timeStamp value }
    tirePressureStatusFrontRight { timeStamp value }
    tirePressureRearLeft { timeStamp value }
    tirePressureRearRight { timeStamp value }
    batteryLevel { timeStamp value }
    batteryLimit { timeStamp value }
    batteryCapacity { timeStamp value }
    chargerState { timeStamp value }
    timeToEndOfCharge { timeStamp value }
    distanceToEmpty { timeStamp value }
    vehicleMileage { timeStamp value }
    battery12VState { timeStamp value }
    cabinClimateInteriorTemperature { timeStamp value }
  }
  getVehicle(id: $vehicleID) {
    chargingSchedules {
      startTime
      duration
      enabled
    }
  }
}"""

    payload = {
        "operationName": "GetVehicleState",
        "query": primary_query,
        "variables": {"vehicleID": vehicle_id},
    }

    resp = session.post(URL, json=payload, headers=headers, timeout=15)
    body = resp.json()

    if resp.status_code == 400 or any(
        err.get("extensions", {}).get("code") == "GRAPHQL_VALIDATION_FAILED"
        for err in body.get("errors", [])
    ):
        fallback_query = """query GetVehicleState($vehicleID: String!) {
  vehicleState(id: $vehicleID) {
    __typename
    gnssLocation { latitude longitude timeStamp }
    tirePressureStatusFrontLeft { timeStamp value }
    tirePressureStatusFrontRight { timeStamp value }
    tirePressureStatusRearLeft { timeStamp value }
    tirePressureStatusRearRight { timeStamp value }
    batteryLevel { timeStamp value }
    batteryLimit { timeStamp value }
    batteryCapacity { timeStamp value }
    chargerState { timeStamp value }
    timeToEndOfCharge { timeStamp value }
    distanceToEmpty { timeStamp value }
    vehicleMileage { timeStamp value }
    twelveVoltBatteryHealth { timeStamp value }
    cabinClimateInteriorTemperature { timeStamp value }
  }
  getVehicle(id: $vehicleID) {
    chargingSchedules {
      startTime
      duration
      enabled
    }
  }
}"""
        fallback_payload = {
            "operationName": "GetVehicleState",
            "query": fallback_query,
            "variables": {"vehicleID": vehicle_id},
        }
        resp = session.post(URL, json=fallback_payload, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()

    if "errors" in body:
        err_msg = body["errors"][0].get("message", "Unknown error")
        raise RuntimeError(f"GetVehicleState failed: {err_msg}")

    raw_state = (body.get("data") or {}).get("vehicleState") or {}
    raw_vehicle = (body.get("data") or {}).get("getVehicle") or {}
    schedules = raw_vehicle.get("chargingSchedules") or []
    sched_start = None
    for s_item in schedules:
        if isinstance(s_item, dict) and s_item.get("startTime") is not None:
            if s_item.get("enabled") is False:
                continue
            sched_start = s_item.get("startTime")
            break
    if sched_start is None and schedules and isinstance(schedules[0], dict):
        sched_start = schedules[0].get("startTime")

    def extract_val(field_name: str, alt_name: Optional[str] = None) -> Any:
        node = raw_state.get(field_name)
        if node is None and alt_name:
            node = raw_state.get(alt_name)
        if isinstance(node, dict):
            return node.get("value")
        return node

    parsed = {
        "tirePressureFrontLeft": extract_val(
            "tirePressureFrontLeft", "tirePressureStatusFrontLeft"
        ),
        "tirePressureFrontRight": extract_val(
            "tirePressureFrontRight", "tirePressureStatusFrontRight"
        ),
        "tirePressureRearLeft": extract_val(
            "tirePressureRearLeft", "tirePressureStatusRearLeft"
        ),
        "tirePressureRearRight": extract_val(
            "tirePressureRearRight", "tirePressureStatusRearRight"
        ),
        "batteryLevel": extract_val("batteryLevel"),
        "batteryLimit": extract_val("batteryLimit"),
        "batteryCapacity": extract_val("batteryCapacity"),
        "chargerState": extract_val("chargerState"),
        "timeToEndOfCharge": extract_val("timeToEndOfCharge"),
        "scheduleStartTime": sched_start,
        "distanceToEmpty": extract_val("distanceToEmpty"),
        "vehicleMileage": extract_val("vehicleMileage"),
        "battery12VState": extract_val("battery12VState", "twelveVoltBatteryHealth"),
        "batteryTemperature": None,
        "batteryTempMin": None,
        "batteryTempMax": None,
        "cabinClimateInteriorTemperature": extract_val(
            "cabinClimateInteriorTemperature"
        ),
        "cabinClimateExteriorTemperature": None,
    }

    # If any fields are None (moved to Parallax on R2 / Gen2 vehicles), pull via Parallax WebSocket
    missing_keys = [k for k, v in parsed.items() if v is None]
    if missing_keys and user_session_token:
        px_data = fetch_parallax_websocket_telemetry(user_session_token, vehicle_id)
        for k, v in px_data.items():
            if parsed.get(k) is None and v is not None:
                parsed[k] = v

    # When parked, Rivian omits field 2 (exterior_temperature) from comfort.cabin.cabin_temperatures.
    # Fall back to external temperature at the vehicle's live gnssLocation coordinates.
    if parsed.get("cabinClimateExteriorTemperature") is None:
        gnss = raw_state.get("gnssLocation") or {}
        lat = gnss.get("latitude")
        lon = gnss.get("longitude")
        if lat is not None and lon is not None:
            try:
                w_resp = session.get(
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": round(float(lat), 4),
                        "longitude": round(float(lon), 4),
                        "current": "temperature_2m",
                    },
                    timeout=5,
                )
                if w_resp.status_code == 200:
                    ext_c = (w_resp.json().get("current") or {}).get("temperature_2m")
                    if ext_c is not None:
                        parsed["cabinClimateExteriorTemperature"] = round(
                            float(ext_c), 1
                        )
            except Exception:
                pass

    return parsed


def celsius_to_fahrenheit(temp_c: Optional[float]) -> Optional[float]:
    """Convert Celsius temperature to Fahrenheit."""
    if temp_c is None:
        return None
    try:
        return round((float(temp_c) * 9.0 / 5.0) + 32.0, 1)
    except (TypeError, ValueError):
        return None


def main() -> None:
    print("=" * 65)
    print(" Rivian R2 Telemetry Client")
    print("=" * 65)

    try:
        config = load_config()
    except Exception as exc:
        print(f"[!] Config Error: {exc}")
        sys.exit(1)

    with requests.Session() as session:
        # 1. Authenticate & refresh token
        print("[1/3] Refreshing access token via Rivian GraphQL gateway...")
        try:
            headers = authenticate_with_refresh_token(session, config)
            print("      Access token refreshed and config.json updated.")
        except Exception as exc:
            print(f"[!] Authentication Error: {exc}")
            sys.exit(1)

        # 2. Retrieve R2 Vehicle ID & Born On Date (actualGeneralAssemblyDate)
        print("[2/3] Querying user vehicles for Rivian R2 ID & Born On date...")
        try:
            vehicle_id, vehicle_desc, born_on_date = get_r2_vehicle_and_born_on_date(
                session, headers, config
            )
            print(f"      Found Vehicle ID: {vehicle_id} ({vehicle_desc})")
            print(f"      Born On Date (actualGeneralAssemblyDate): {born_on_date} (cached in config.json)")
        except Exception as exc:
            print(f"[!] Vehicle Lookup Error: {exc}")
            sys.exit(1)

        # 3. Fetch Vehicle Telemetry (HTTP GetVehicleState + Parallax WebSocket)
        print(f"[3/3] Fetching GetVehicleState + Parallax telemetry for {vehicle_id}...")
        try:
            telemetry = fetch_vehicle_telemetry(
                session,
                headers,
                vehicle_id,
                user_session_token=config.get("user_session_token"),
            )
        except Exception as exc:
            print(f"[!] Telemetry Fetch Error: {exc}")
            sys.exit(1)

    # 4. Convert units and print results
    temp_c = telemetry.get("cabinClimateInteriorTemperature")
    temp_f = celsius_to_fahrenheit(temp_c)
    if temp_f is not None:
        temp_display = f"{temp_f}°F ({temp_c}°C)"
    else:
        temp_display = "None"

    ext_c = telemetry.get("cabinClimateExteriorTemperature")
    ext_f = celsius_to_fahrenheit(ext_c)
    ext_display = f"{ext_f}°F ({ext_c}°C)" if ext_f is not None else "None"

    batt_temp_c = telemetry.get("batteryTemperature")
    batt_temp_f = celsius_to_fahrenheit(batt_temp_c)
    batt_min_f = celsius_to_fahrenheit(telemetry.get("batteryTempMin"))
    batt_max_f = celsius_to_fahrenheit(telemetry.get("batteryTempMax"))
    if batt_temp_f is not None:
        batt_temp_display = (
            f"{batt_temp_f}°F avg (min {batt_min_f}°F / max {batt_max_f}°F)"
            if (batt_min_f is not None and batt_max_f is not None)
            else f"{batt_temp_f}°F ({batt_temp_c}°C)"
        )
    else:
        batt_temp_display = "None"

    dte_km = telemetry.get("distanceToEmpty")
    dte_display = (
        f"{round(float(dte_km) * KM_TO_MILES)} mi ({dte_km} km)"
        if dte_km is not None
        else "None"
    )

    odo_m = telemetry.get("vehicleMileage")
    odo_display = (
        f"{round(float(odo_m) * METERS_TO_MILES):,} mi ({odo_m:,} m)"
        if odo_m is not None
        else "None"
    )

    batt = telemetry.get("batteryLevel")
    batt_display = f"{round(float(batt), 1)}%" if batt is not None else "None"

    def fmt_psi(val: Any) -> str:
        return f"{val} PSI" if isinstance(val, (int, float)) else str(val)

    print("\n" + "=" * 65)
    print(f" RIVIAN R2 TELEMETRY REPORT (Vehicle ID: {vehicle_id})")
    print("=" * 65)
    print(f"  tirePressureFrontLeft           : {fmt_psi(telemetry['tirePressureFrontLeft'])}")
    print(f"  tirePressureFrontRight          : {fmt_psi(telemetry['tirePressureFrontRight'])}")
    print(f"  tirePressureRearLeft            : {fmt_psi(telemetry['tirePressureRearLeft'])}")
    print(f"  tirePressureRearRight           : {fmt_psi(telemetry['tirePressureRearRight'])}")
    print(f"  batteryLevel                    : {batt_display}")
    print(f"  chargerState                    : {telemetry['chargerState']}")
    print(f"  distanceToEmpty                 : {dte_display}")
    print(f"  vehicleMileage                  : {odo_display}")
    print(f"  battery12VState                 : {telemetry['battery12VState']}")
    print(f"  batteryTemperature              : {batt_temp_display}")
    print(f"  cabinClimateInteriorTemperature : {temp_display}")
    print(f"  cabinClimateExteriorTemperature : {ext_display}")
    print(f"  actualGeneralAssemblyDate       : {born_on_date} (Born On)")
    print("=" * 65)


if __name__ == "__main__":
    main()
