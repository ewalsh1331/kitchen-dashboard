"""Shared Google sign-in for the Pi's tools (uses token.json from auth.py)."""
import json
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

BASE = Path(__file__).resolve().parent.parent
CONFIG = json.loads((BASE / "config.json").read_text())
TOKEN_PATH = BASE / "token.json"


def credentials() -> Credentials:
    if not TOKEN_PATH.exists():
        raise SystemExit("Not signed in to Google. Run: .venv/bin/python auth.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), CONFIG["scopes"])
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_PATH.write_text(creds.to_json())
        else:
            raise SystemExit("Google sign-in expired. Run: .venv/bin/python auth.py")
    return creds
