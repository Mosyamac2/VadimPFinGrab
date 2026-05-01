#!/usr/bin/env python3
"""Standalone helper: obtain a Google Drive refresh token from a machine
that HAS a browser (your laptop).

Use this when your edx server is headless / SSH-only. After running this
script locally, copy the printed refresh token into the **server's** .env
as ``GOOGLE_OAUTH_REFRESH_TOKEN=...``.

The refresh token is portable: it works on any machine that uses the
same OAuth client (the one whose client_id and client_secret you pass to
this script).

Local prerequisites:
    pip install google-auth-oauthlib

Usage:
    python get_drive_token.py CLIENT_ID CLIENT_SECRET

You can also drop the IDs into env vars and run without arguments:
    GOOGLE_OAUTH_CLIENT_ID=... GOOGLE_OAUTH_CLIENT_SECRET=... \\
        python get_drive_token.py
"""
from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def main(client_id: str, client_secret: str) -> int:
    if not client_id or not client_secret:
        print(
            "ERROR: client_id and client_secret are required.\n"
            "Get them from "
            "https://console.cloud.google.com/ → APIs & Services → "
            "Credentials → your OAuth Desktop client.",
            file=sys.stderr,
        )
        return 2

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uris": ["http://localhost"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    print(
        "A browser window will open. Sign in with the Gmail account that "
        "owns the target Drive folder, then accept the permission prompt.\n"
    )
    creds = flow.run_local_server(port=0)
    if not creds.refresh_token:
        print(
            "\nERROR: Google did not return a refresh token. Revoke previous "
            "app access at https://myaccount.google.com/permissions and "
            "re-run this script.",
            file=sys.stderr,
        )
        return 1

    print("\n" + "=" * 60)
    print("GOOGLE_OAUTH_REFRESH_TOKEN — paste this into the server's .env")
    print("=" * 60)
    print(creds.refresh_token)
    print("=" * 60)
    print(
        "\nServer-side .env should look like:\n"
        "  GOOGLE_OAUTH_CLIENT_ID=...\n"
        "  GOOGLE_OAUTH_CLIENT_SECRET=...\n"
        f"  GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}\n"
    )
    return 0


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        client_id = sys.argv[1]
        client_secret = sys.argv[2]
    else:
        client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
    sys.exit(main(client_id, client_secret))
