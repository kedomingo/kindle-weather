#!/usr/bin/env python3
"""One-time OAuth consent, to get the refresh token the Pi will use.

Only needed for a *private* calendar. If you can put the events on a calendar
you make public, skip this entirely: set calendar_id in the config and gcal.py
reads the public .ics feed with no credentials at all.

Run this on a machine with a browser -- your laptop, not the Pi -- then paste
the printed block into the Pi's config:

    ./gcal_setup.py --client-id XXX.apps.googleusercontent.com --client-secret YYY

Google turned off the copy-a-code-into-the-terminal flow ("oob") in 2022, so
this spins up a loopback server to catch the redirect instead. Create the OAuth
client as an **installed/desktop app**: those accept http://localhost on any
port, so there is no redirect URI to register.

Before running, on the OAuth consent screen: add the calendar.readonly scope,
add yourself as a user, and set the publishing status to "In production".
Leaving it in "Testing" caps refresh tokens at 7 days.
"""

from __future__ import annotations

import argparse
import http.server
import json
import sys
import urllib.parse
import urllib.request
import webbrowser

from gcal import SCOPE, TOKEN_URL

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"


class _Catcher(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _Catcher.code = (query.get("code") or [None])[0]
        _Catcher.error = (query.get("error") or [None])[0]
        body = b"Done - you can close this tab and go back to the terminal."
        if _Catcher.error:
            body = f"Consent failed: {_Catcher.error}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass  # the redirect carries the auth code; keep it out of the scrollback


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-id", required=True)
    ap.add_argument("--client-secret", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--calendar-id", default="primary")
    args = ap.parse_args(argv)

    redirect = f"http://localhost:{args.port}"
    consent = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": args.client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        # offline gets a refresh token at all; consent forces a fresh one even
        # if this account has already approved the app once.
        "access_type": "offline",
        "prompt": "consent",
    })

    print("Opening your browser to approve read-only calendar access.")
    print(f"If it does not open, visit:\n\n  {consent}\n")
    server = http.server.HTTPServer(("localhost", args.port), _Catcher)
    webbrowser.open(consent)
    server.handle_request()
    server.server_close()

    if _Catcher.error or not _Catcher.code:
        print(f"consent failed: {_Catcher.error or 'no code returned'}", file=sys.stderr)
        return 1

    body = urllib.parse.urlencode({
        "code": _Catcher.code,
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }).encode()
    with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body, method="POST"),
                               timeout=20) as response:
        tokens = json.load(response)

    refresh = tokens.get("refresh_token")
    if not refresh:
        print("Google returned no refresh token. That happens when the account "
              "has already granted this client and prompt=consent was dropped; "
              "revoke the app at myaccount.google.com/permissions and retry.",
              file=sys.stderr)
        return 1

    print("\nAdd this to the config on the Pi:\n")
    print(json.dumps({"calendar": {
        "client_id": args.client_id,
        "client_secret": args.client_secret,
        "refresh_token": refresh,
        "calendar_id": args.calendar_id,
        "max_events": 2,
    }}, indent=2))
    print("\nKeep it out of git: the refresh token is a standing key to your calendar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
