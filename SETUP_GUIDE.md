# Kitchen Dashboard — Setup Guide

Phase 1 goal: the Pi boots straight into a full-screen page showing the clock, **"Connected ✓"**, and your next 3 Claude Calendar events, pulled live from Google. Total hands-on time is about 60–90 minutes.

**Do Part A now, while the Pi ships.** Parts B–E need the Pi.

| Part | What | Where | Time |
|---|---|---|---|
| A | Google Cloud sign-in setup | PC browser | 15 min |
| B | Flash the microSD card | PC | 15 min |
| C | First boot + copy the code | Pi + PC | 15 min |
| D | Install, sign in to Google, reboot | PC terminal (SSH) | 20 min |
| E | Remote access with Tailscale (optional) | Pi + PC + phone | 10 min |

---

## Part A — Google Cloud sign-in (do this now)

This lets the Pi read your calendar (and later the Fitness Data sheet) directly, without using Claude.

1. Go to **console.cloud.google.com**, signed in as e.walsh1331@gmail.com. Accept the terms if prompted.
2. **Create a project:** project picker (top left) → **New project** → name it `kitchen-dashboard` → Create. Make sure it's selected afterwards.
3. **Turn on the APIs:** ☰ menu → **APIs & Services → Library**. Search and **Enable** both:
    - Google Calendar API
    - Google Sheets API
4. **Set up the consent screen:** ☰ → **Google Auth Platform** (older UI: *OAuth consent screen*) → **Get started**.
    - App name: `kitchen-dashboard`; support email: your Gmail.
    - Audience: **External**.
    - Contact email: your Gmail. Agree and **Create**.
5. **Publish it (important):** Google Auth Platform → **Audience** → **Publish app** → Confirm. Status should read **In production**.
    - Why: apps left in "Testing" lose their sign-in every 7 days, which would quietly break the screen.
    - You do **not** need Google's verification for personal use. You'll just see an "unverified app" warning once when signing in.
6. **Create the client key:** Google Auth Platform → **Clients** → **Create client**.
    - Application type: **Desktop app**; name: `kitchen-pi`. Create.
    - **Download JSON**, then rename the file to exactly `client_secret.json`. Keep it in your Downloads folder.
    - Treat this file like a password. Don't email it or post it anywhere.

Also install **Raspberry Pi Imager** on your PC now (raspberrypi.com/software).

---

## Part B — Flash the microSD card

The kit's card comes preloaded, but re-flashing lets you set Wi-Fi and SSH so you never need a keyboard on the Pi.

1. Put the microSD in the kit's card reader and plug it into your PC.
2. Open **Raspberry Pi Imager**:
    - Device: **Raspberry Pi 5**
    - OS: **Raspberry Pi OS (64-bit)**, the standard one with desktop (not Lite)
    - Storage: the microSD card
3. When asked to customize, choose **Edit settings**:
    - Hostname: `kitchen-pi`
    - Username `eric` and a password you'll remember
    - Wi-Fi name and password, country **US**
    - Time zone **America/Chicago**, keyboard **us**
    - **Services** tab: enable **SSH** (password authentication)
4. Save → Yes → Write. Wait for it to finish verifying, then remove the card.

---

## Part C — First boot and copy the code

1. Put the card in the Pi, connect the monitor (micro-HDMI port **0**, the one next to the USB-C power), then plug in power. Wait **3 minutes** for the first boot.
2. On your PC, open **PowerShell** and connect:

    ```
    ssh eric@kitchen-pi.local
    ```

    Type `yes` to trust it, then your Pi password.

3. Update the Pi (5–10 min), then it reboots and disconnects you:

    ```
    sudo apt update && sudo apt full-upgrade -y && sudo reboot
    ```

4. Unzip `kitchen-dashboard.zip` into your Downloads folder. In a **new PowerShell window on your PC** (not in SSH), copy the code and your Google key to the Pi:

    ```
    scp -r $HOME\Downloads\kitchen-dashboard eric@kitchen-pi.local:~/
    scp $HOME\Downloads\client_secret.json eric@kitchen-pi.local:~/kitchen-dashboard/
    ```

---

## Part D — Install, sign in, reboot

1. Connect **with a port forward**, which is needed for the Google sign-in step:

    ```
    ssh -L 8765:localhost:8765 eric@kitchen-pi.local
    ```

2. Run the installer (about 5 minutes). It sets up Python, a service that starts on boot, the full-screen browser, and turns off screen blanking:

    ```
    cd ~/kitchen-dashboard
    bash deploy/setup.sh
    ```

3. Sign in to Google:

    ```
    .venv/bin/python auth.py
    ```

    - Copy the link it prints and open it in your **PC's** browser.
    - Pick your Gmail account.
    - On "Google hasn't verified this app": **Advanced → Go to kitchen-dashboard (unsafe)**. It's your own app.
    - Allow both read-only permissions. The terminal prints *Saved token.json*.

4. Restart the service and test it:

    ```
    sudo systemctl restart kitchen-dashboard
    curl -s localhost:8080/api/status
    ```

    You should see `"ok": true` and your upcoming events.

5. Reboot: `sudo reboot`

**Milestone check:** within about a minute of reboot, the monitor shows the clock, a green **Connected ✓**, and your next 3 Claude Calendar events. The screen dims itself between 11 PM and 6 AM.

---

## Part E — Remote access with Tailscale (optional, recommended)

This lets you (and Claude Code later) reach the Pi from anywhere, without opening router ports.

1. On the Pi (over SSH):

    ```
    curl -fsSL https://tailscale.com/install.sh | sh
    sudo tailscale up
    ```

    Open the link it prints and sign in. A free personal account is fine.

2. Install Tailscale on your PC and phone, signed in to the same account.
3. From anywhere: `ssh eric@kitchen-pi`

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `kitchen-pi.local` not found | Wait another 2 minutes. Otherwise find the Pi's IP in your router's device list and use `ssh eric@<ip>` |
| Screen shows desktop, not the dashboard | Run `~/kitchen-dashboard/deploy/kiosk.sh` from the Pi's desktop. If that works, the autostart file isn't being picked up; tell Claude your Pi OS version |
| Red "Dashboard service not responding" | `sudo systemctl status kitchen-dashboard` and `journalctl -u kitchen-dashboard -n 50` |
| Red "Google connection problem" mentioning `invalid_grant` | The app was left in Testing, or sign-in was revoked. Publish it (Part A step 5), then re-run step D3 |
| Need to exit full-screen | Over SSH: `pkill chromium`. Reboot to bring it back |
| Screen still goes black | `sudo raspi-config` → Display Options → Screen Blanking → **No**, then reboot |

## Changing the dashboard later

The layout is one file: `static/index.html`. Settings like the number of events and the calendar are in `config.json`. After editing on the Pi:

```
sudo systemctl restart kitchen-dashboard
sudo reboot
```

Once Claude Code is on the Pi (next phase), it makes these changes for you.
