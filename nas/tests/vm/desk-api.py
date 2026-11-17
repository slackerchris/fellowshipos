#!/usr/bin/python3
"""Talk to the installed NAS's desktop API as a browser does: sign in as the
NAS admin, then run fnas commands with the session's CSRF token. Usage:
  desk.py login
  desk.py fnas network apply --dhcp
  desk.py fnas --json network show
"""
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.request

BASE = os.environ.get("FELLOWSHIP_BASE", "https://127.0.0.1:8443")
VERIFY = os.environ.get("FELLOWSHIP_VERIFY_DIR", "/tmp/nas-verify")
JAR = os.path.join(VERIFY, "cookies.txt")
CSRF = os.path.join(VERIFY, "csrf.txt")

context = ssl.create_default_context()
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.MozillaCookieJar(JAR)
try:
    jar.load(ignore_discard=True, ignore_expires=True)
except OSError:
    pass
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                     urllib.request.HTTPSHandler(context=context))


def call(method, path, body=None, csrf=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "Origin": BASE}
    if csrf:
        headers["X-Fellowship-Token"] = csrf
    request = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with opener.open(request, timeout=60) as answer:
            return answer.status, json.load(answer)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def main():
    if sys.argv[1] == "login":
        status, answer = call("POST", "/api/login", {"user": "kim", "password": "kim verify pw 1"})
        print(status, json.dumps(answer) if isinstance(answer, dict) else answer)
        if status == 200:
            with open(CSRF, "w") as file:
                file.write(answer["csrf"])
            jar.save(ignore_discard=True, ignore_expires=True)
        return
    with open(CSRF) as file:
        csrf = file.read().strip()
    try:
        status, answer = call("POST", "/api/fnas", {"args": sys.argv[2:]}, csrf)
    except (urllib.error.URLError, OSError) as error:
        # An address change cuts the connection under the response: expected
        print("connection lost:", error)
        return
    print(status)
    print(json.dumps(answer, indent=2) if isinstance(answer, (dict, list)) else answer)


if __name__ == "__main__":
    main()
