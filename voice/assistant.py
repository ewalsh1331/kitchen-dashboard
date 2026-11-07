"""Kitchen voice assistant (prototype).

Pipeline: wake word ("Hey Jarvis", on-device) or a trigger file
  -> record until you stop talking -> Whisper speech-to-text (on-device)
  -> Claude Code with the kitchen tools and CLAUDE.md rules
  -> Piper text-to-speech (on-device) -> speaker.
Only the TEXT of a request leaves the Pi (to Claude). No audio does.

Commands (run from ~/kitchen-dashboard):
  voice/.venv/bin/python voice/assistant.py devices          # list mics/speakers
  voice/.venv/bin/python voice/assistant.py say "hello"      # test the voice
  voice/.venv/bin/python voice/assistant.py test FILE.wav    # full pipeline on a recording
  voice/.venv/bin/python voice/assistant.py run              # listen for the wake word
  voice/.venv/bin/python voice/assistant.py listen           # "button": start listening now

Settings via environment variables (or voice/voice.env):
  VOICE_MIC=plughw:CARD=Headset,DEV=0   ALSA capture device (default: "default")
  VOICE_SPK=plughw:CARD=Headset,DEV=0   ALSA playback device (default: "default")
  VOICE_WAKE_THRESHOLD=0.5              higher = fewer false triggers
"""
import json
import math
import os
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MODELS = HERE / "models"
TRIGGER = HERE / ".listen-now"
LOG = HERE / "voice.log"
RATE = 16000
CHUNK = 1280  # 80 ms, what openWakeWord expects

env_file = HERE / "voice.env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

MIC = os.environ.get("VOICE_MIC", "default")
SPK = os.environ.get("VOICE_SPK", "default")
WAKE_THRESHOLD = float(os.environ.get("VOICE_WAKE_THRESHOLD", "0.5"))
CLAUDE = os.path.expanduser("~/.local/bin/claude")
FOLLOWUP_SECONDS = 120  # a reply within this window continues the same conversation

VOICE_PROMPT = """This request came from Eric's kitchen voice assistant: he spoke it
aloud and your reply will be read out by a text-to-speech voice.
- Reply in plain spoken sentences: no markdown, tables, bullet points, emoji or URLs.
- Keep it to one to three short sentences unless he asks for detail.
- Say numbers and times the way a person would ("six PM", "eighty-one").
- Follow CLAUDE.md as usual. Adding to the pending list can go straight in.
  Calendar changes and workout-log uploads still need a yes: read the details back
  in one sentence and end with a yes-or-no question. His next utterance is the answer.
- Speech-to-text can mishear words; if a name or number seems off, ask."""

ALLOWED_TOOLS = [
    "Bash(.venv/bin/python tools/gcal.py list:*)",
    "Bash(.venv/bin/python tools/gcal.py get:*)",
    "Bash(.venv/bin/python tools/gcal.py create:*)",
    "Bash(.venv/bin/python tools/gcal.py update:*)",
    "Bash(.venv/bin/python tools/gcal.py pending:*)",
    "Bash(.venv/bin/python tools/fitness.py:*)",
    "Bash(.venv/bin/python tools/workout.py pull:*)",
    "Bash(.venv/bin/python tools/workout.py show:*)",
    "Bash(.venv/bin/python tools/workout.py day:*)",
    "Bash(.venv/bin/python tools/workout.py log:*)",
    "Bash(.venv/bin/python tools/workout.py track:*)",
    "Bash(.venv/bin/python tools/workout.py push:*)",
]


def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with LOG.open("a") as f:
        f.write(line + "\n")


# --- Text to speech (Piper) -------------------------------------------------

_voice = None


def tts_to_wav(text, path):
    global _voice
    from piper import PiperVoice
    if _voice is None:
        _voice = PiperVoice.load(str(MODELS / "piper" / "en_US-lessac-medium.onnx"))
    with wave.open(str(path), "wb") as wf:
        _voice.synthesize_wav(text, wf)


def play(path):
    subprocess.run(["aplay", "-q", "-D", SPK, str(path)], check=False)


def say(text, out=None):
    path = Path(out) if out else Path(tempfile.mkstemp(suffix=".wav")[1])
    tts_to_wav(text, path)
    if not out:
        play(path)
        path.unlink(missing_ok=True)


def beep(freq=880, ms=150):
    path = Path(tempfile.mkstemp(suffix=".wav")[1])
    n = int(RATE * ms / 1000)
    frames = b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * freq * i / RATE)
                                            * min(1, i / 400, (n - i) / 400))) for i in range(n))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(RATE); wf.writeframes(frames)
    play(path)
    path.unlink(missing_ok=True)


# --- Speech to text (Whisper) ----------------------------------------------

_whisper = None


def load_wav_16k(path):
    """Read a WAV as mono float32 at 16 kHz (avoids faster-whisper's PyAV decoder,
    which breaks with newer PyAV versions)."""
    import numpy as np
    from scipy.signal import resample_poly
    with wave.open(str(path), "rb") as wf:
        ch, rate, width = wf.getnchannels(), wf.getframerate(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError("expected 16-bit WAV")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch).mean(axis=1)
    if rate != RATE:
        g = math.gcd(rate, RATE)
        audio = resample_poly(audio, RATE // g, rate // g).astype(np.float32)
    return audio


def transcribe(path):
    global _whisper
    from faster_whisper import WhisperModel
    if _whisper is None:
        _whisper = WhisperModel("base.en", device="cpu", compute_type="int8",
                                download_root=str(MODELS / "whisper"))
    segments, _ = _whisper.transcribe(load_wav_16k(path), language="en", vad_filter=True, beam_size=1)
    return " ".join(s.text.strip() for s in segments).strip()


# --- Claude ------------------------------------------------------------------

_session = {"id": None, "at": 0.0}


def ask_claude(text):
    cmd = [CLAUDE, "-p", text, "--output-format", "json", "--max-turns", "15",
           "--append-system-prompt", VOICE_PROMPT, "--allowedTools", *ALLOWED_TOOLS]
    if _session["id"] and time.time() - _session["at"] < FOLLOWUP_SECONDS:
        cmd[1:1] = ["--resume", _session["id"]]
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=180)
        data = json.loads(r.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        log(f"claude error: {e}")
        return "Sorry, that took too long. Try again, or ask on your phone."
    _session.update(id=data.get("session_id"), at=time.time())
    return (data.get("result") or "Sorry, I didn't get an answer.").strip()


def handle(wav_path):
    t0 = time.time()
    text = transcribe(wav_path)
    t1 = time.time()
    if not text:
        log(f"heard nothing ({t1 - t0:.1f}s)")
        return None, "Sorry, I didn't catch that."
    log(f"heard: {text!r} ({t1 - t0:.1f}s)")
    answer = ask_claude(text)
    log(f"answer: {answer!r} ({time.time() - t1:.1f}s)")
    return text, answer


# --- Microphone ----------------------------------------------------------------

def mic_stream():
    return subprocess.Popen(["arecord", "-q", "-D", MIC, "-f", "S16_LE", "-r", str(RATE),
                             "-c", "1", "-t", "raw"], stdout=subprocess.PIPE)


def rms(buf):
    n = len(buf) // 2
    if not n:
        return 0.0
    s = struct.unpack(f"<{n}h", buf[:n * 2])
    return math.sqrt(sum(x * x for x in s) / n)


def record_utterance(max_s=12, wait_s=6, silence_s=1.0):
    """Record from the mic until ~1 s of silence after speech. Returns a wav path or None."""
    proc = mic_stream()
    frames, noise, started, quiet, t0 = [], [], False, 0.0, time.time()
    try:
        while time.time() - t0 < max_s:
            buf = proc.stdout.read(CHUNK * 2)
            if not buf:
                break
            level = rms(buf)
            if len(noise) < 4:            # first 0.3 s: measure background noise
                noise.append(level)
                continue
            threshold = max(sum(noise) / len(noise) * 3, 400)
            if level > threshold:
                started, quiet = True, 0.0
            elif started:
                quiet += CHUNK / RATE
            if started:
                frames.append(buf)
                if quiet >= silence_s:
                    break
            elif time.time() - t0 > wait_s:
                return None
    finally:
        proc.terminate()
    if not frames:
        return None
    path = Path(tempfile.mkstemp(suffix=".wav")[1])
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(RATE)
        wf.writeframes(b"".join(frames))
    return path


def converse():
    """One exchange, plus follow-ups while Claude is asking a question."""
    while True:
        beep()
        wav = record_utterance()
        if wav is None:
            beep(440)
            return
        _, answer = handle(wav)
        wav.unlink(missing_ok=True)
        say(answer)
        if not answer.rstrip().endswith("?"):
            return


def run():
    import numpy as np
    from openwakeword.model import Model
    wake = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    log(f"listening for 'Hey Jarvis' (mic={MIC}, speaker={SPK}, threshold={WAKE_THRESHOLD})")
    while True:
        proc = mic_stream()
        triggered = False
        try:
            while not triggered:
                buf = proc.stdout.read(CHUNK * 2)
                if not buf:
                    log("microphone stopped; retrying in 5 s")
                    time.sleep(5)
                    break
                if TRIGGER.exists():
                    TRIGGER.unlink(missing_ok=True)
                    log("triggered by 'listen'")
                    triggered = True
                    break
                scores = wake.predict(np.frombuffer(buf, dtype=np.int16))
                if max(scores.values()) >= WAKE_THRESHOLD:
                    log(f"wake word ({max(scores.values()):.2f})")
                    triggered = True
        finally:
            proc.terminate()
        if triggered:
            converse()
            wake.reset()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "devices":
        subprocess.run(["arecord", "-l"]); subprocess.run(["aplay", "-l"])
    elif cmd == "say":
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        say(sys.argv[2], out)
    elif cmd == "test":
        _, answer = handle(Path(sys.argv[2]))
        out = HERE / "test-answer.wav"
        tts_to_wav(answer, out)
        print(f"ANSWER: {answer}\n(spoken version saved to {out})")
    elif cmd == "listen":
        TRIGGER.touch()
        print("Listening now (if the assistant is running).")
    elif cmd == "run":
        run()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
