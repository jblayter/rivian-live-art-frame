import warnings
warnings.filterwarnings("ignore")

import requests
import json
import getpass
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

URL = "https://rivian.com/api/gql/gateway/graphql"
BASE_HEADERS = {
    "User-Agent": "RivianApp/707 CFNetwork/1237 Darwin/20.4.0",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Apollographql-Client-Name": "com.rivian.ios.consumer-apollo-ios",
}

def get_csrf_tokens():
    """Fetches initial CSRF and App Session tokens required by Rivian gateway."""
    csrf_payload = {
        "operationName": "CreateCSRFToken",
        "query": "mutation CreateCSRFToken {\n  createCsrfToken {\n    __typename\n    csrfToken\n    appSessionToken\n  }\n}",
        "variables": None
    }
    resp = requests.post(URL, json=csrf_payload, headers=BASE_HEADERS)
    resp.raise_for_status()
    data = resp.json().get("data", {}).get("createCsrfToken", {})
    return data.get("csrfToken"), data.get("appSessionToken")

def get_rivian_token():
    print("=" * 60)
    print(" Rivian Authentication & Token Generator")
    print("=" * 60)
    
    # 1. Fetch CSRF & App Session
    print("[1/3] Initializing session with Rivian gateway...")
    try:
        csrf_token, app_sess = get_csrf_tokens()
        if not csrf_token or not app_sess:
            print("ERROR: Failed to retrieve CSRF tokens from Rivian.")
            return
    except Exception as e:
        print(f"ERROR: Unable to connect to Rivian API: {e}")
        return

    headers = BASE_HEADERS.copy()
    headers["Csrf-Token"] = csrf_token
    headers["A-Sess"] = app_sess

    # 2. Collect credentials
    email = input("Rivian Email: ").strip()
    
    print("Note: Terminal password prompts do not display characters or asterisks while typing/pasting.")
    password = getpass.getpass("Rivian Password: ").strip()
    if not password:
        print("ERROR: Password cannot be empty.")
        return
    print(f"Password captured: ✓ ({len(password)} characters)")

    # 3. Initial Login Mutation (uses JSON-escaped literals to satisfy Rivian gateway parser)
    print("\n[2/3] Submitting login request...")
    clean_email = json.dumps(email)
    clean_password = json.dumps(password)

    login_query = f"""mutation Login {{
  login(email: {clean_email}, password: {clean_password}) {{
    __typename
    ... on MobileLoginResponse {{
      __typename
      accessToken
      refreshToken
      userSessionToken
    }}
    ... on MobileMFALoginResponse {{
      __typename
      otpToken
    }}
  }}
}}"""

    login_resp = requests.post(URL, json={"operationName": "Login", "query": login_query}, headers=headers).json()

    if "errors" in login_resp:
        err_msg = login_resp["errors"][0].get("message", "Unknown error")
        print(f"\n[!] Login failed: {err_msg}")
        return

    login_data = login_resp.get("data", {}).get("login", {})
    typename = login_data.get("__typename")

    refresh_token = None
    user_session_token = None
    access_token = None

    # Check for Mandatory 2FA/MFA
    if typename == "MobileMFALoginResponse":
        otp_token = login_data.get("otpToken")
        print("\n[!] 2FA Required! Check your SMS / email for the one-time passcode.")
        otp_code = input("Enter 6-digit OTP: ").strip()

        print("\n[3/3] Submitting OTP verification...")
        clean_code = json.dumps(otp_code)
        clean_otp_token = json.dumps(otp_token)

        otp_query = f"""mutation LoginWithOTP {{
  loginWithOTP(email: {clean_email}, otpCode: {clean_code}, otpToken: {clean_otp_token}) {{
    __typename
    ... on MobileLoginResponse {{
      __typename
      accessToken
      refreshToken
      userSessionToken
    }}
  }}
}}"""

        otp_resp = requests.post(URL, json={"operationName": "LoginWithOTP", "query": otp_query}, headers=headers).json()

        if "errors" in otp_resp:
            err_msg = otp_resp["errors"][0].get("message", "Unknown OTP error")
            print(f"\n[!] OTP Verification failed: {err_msg}")
            return

        session_data = otp_resp.get("data", {}).get("loginWithOTP", {})
        refresh_token = session_data.get("refreshToken")
        user_session_token = session_data.get("userSessionToken")
        access_token = session_data.get("accessToken")

    elif typename == "MobileLoginResponse":
        refresh_token = login_data.get("refreshToken")
        user_session_token = login_data.get("userSessionToken")
        access_token = login_data.get("accessToken")
    else:
        print("\n[!] Unexpected login response:", login_resp)
        return

    # Save tokens to config.json
    if refresh_token or user_session_token:
        config = {
            "refresh_token": refresh_token,
            "user_session_token": user_session_token,
            "access_token": access_token,
            "csrf_token": csrf_token,
            "app_session_token": app_sess
        }
        cfg_path = os.path.join(REPO_ROOT, "config.json")
        with open(cfg_path, "w") as f:
            json.dump(config, f, indent=4)
        print("\n" + "=" * 60)
        print(f" SUCCESS! {cfg_path} created successfully.")
        if refresh_token:
            print(f" Refresh Token: {refresh_token[:12]}... (saved)")
        if user_session_token:
            print(f" User Session Token: {user_session_token[:12]}... (saved)")
        print("=" * 60)
    else:
        print("\n[!] Failed to extract session tokens.")

if __name__ == "__main__":
    get_rivian_token()
