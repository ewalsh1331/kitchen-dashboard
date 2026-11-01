"""One-time Google sign-in. Creates token.json next to this file.

Run on the Pi over SSH with a port forward from your PC:
    ssh -L 8765:localhost:8765 eric@kitchen-pi.local
    cd ~/kitchen-dashboard && .venv/bin/python auth.py
Then open the printed link in your PC's browser.
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text())

flow = InstalledAppFlow.from_client_secrets_file(
    str(BASE / "client_secret.json"), CONFIG["scopes"]
)
creds = flow.run_local_server(
    port=8765,
    open_browser=False,
    access_type="offline",
    prompt="consent",
    authorization_prompt_message="\nOpen this link in your PC's browser:\n{url}\n",
    success_message="Signed in. You can close this tab and go back to the terminal.",
)
(BASE / "token.json").write_text(creds.to_json())
print("Saved token.json. Google sign-in complete.")
