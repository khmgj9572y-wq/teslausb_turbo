#!/usr/bin/env python3
"""Authenticated TeslaUSB CGI bridge for the Wi-Fi picker."""

import json
import os
import subprocess
import sys
from urllib.parse import parse_qs, urlparse


ADMIN = "/usr/local/lib/teslausb-car-link/wifi_admin.py"
GET_ACTIONS = {"status", "scan"}
POST_ACTIONS = {"add", "delete", "permanent", "retry",
                "portal-start", "portal-check", "portal-stop"}


def respond(status, value):
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    sys.stdout.write("Status: {}\r\n".format(status))
    sys.stdout.write("Content-Type: application/json; charset=utf-8\r\n")
    sys.stdout.write("Cache-Control: no-store\r\n")
    sys.stdout.write("X-Content-Type-Options: nosniff\r\n")
    sys.stdout.write("Content-Length: {}\r\n\r\n".format(len(body)))
    sys.stdout.flush()
    sys.stdout.buffer.write(body)


def same_origin():
    origin = os.environ.get("HTTP_ORIGIN")
    if not origin:
        return True
    parsed = urlparse(origin)
    return (parsed.scheme in ("http", "https") and
            parsed.netloc == os.environ.get("HTTP_HOST") and
            parsed.path == "" and not parsed.query and not parsed.fragment)


def main():
    method = os.environ.get("REQUEST_METHOD", "")
    action = parse_qs(os.environ.get("QUERY_STRING", "")).get("action", [""])[0]
    if method == "GET" and action in GET_ACTIONS:
        body = b""
    elif method == "POST" and action in POST_ACTIONS:
        if (os.environ.get("HTTP_X_CAR_WIFI_ACTION") != "1" or
                not os.environ.get("CONTENT_TYPE", "").startswith("application/json") or
                os.environ.get("HTTP_SEC_FETCH_SITE", "same-origin") not in
                ("same-origin", "none") or not same_origin()):
            respond("403 Forbidden", {"ok": False, "error": "Request not allowed"})
            return
        try:
            length = int(os.environ.get("CONTENT_LENGTH", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > 4096:
            respond("413 Content Too Large", {"ok": False, "error": "Request too large"})
            return
        body = sys.stdin.buffer.read(length)
        if len(body) != length:
            respond("400 Bad Request", {"ok": False, "error": "Incomplete request"})
            return
    else:
        respond("405 Method Not Allowed", {"ok": False, "error": "Unknown action"})
        return

    try:
        result = subprocess.run(["sudo", "-n", ADMIN, action], input=body,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=45)
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or "ok" not in response:
            raise ValueError("invalid helper response")
        respond("200 OK" if result.returncode == 0 else "400 Bad Request", response)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        respond("503 Service Unavailable",
                {"ok": False, "error": "Wi-Fi service is unavailable"})


if __name__ == "__main__":
    main()
