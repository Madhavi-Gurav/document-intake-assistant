#!/usr/bin/env python3
"""One-command setup and launch.

    python start.py            # Windows: double-click start.bat | macOS/Linux: ./start.sh

1. Creates a virtual environment in .venv (first run only)
2. Installs dependencies (again only when requirements.txt changes)
3. If there is no API key yet, asks for one, checks it works, picks an available model
   and writes .env
4. Starts the server and opens the app in your browser

Uses only the Python standard library, so it runs before anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".requirements.sha256"

PROVIDERS = {
    "nvidia": {
        "label": "NVIDIA NIM",
        "chat_url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "key_url": "https://build.nvidia.com  (sign in, open any model, click 'Get API Key')",
        # First one that the account can see wins. Tested in September 2026.
        "models": ["nvidia/nemotron-3-super-120b-a12b", "deepseek-ai/deepseek-v4-flash-0731"],
    },
    "gemini": {
        "label": "Google AI Studio",
        "chat_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "key_url": "https://aistudio.google.com/apikey",
        "models": ["gemini-2.5-flash", "gemini-2.0-flash"],
    },
}


def say(msg: str = "") -> None:
    print(msg, flush=True)


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        sys.exit("\nSetup cancelled. Run the launcher again when you have an API key.")


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


# ---- steps -------------------------------------------------------------------

def check_python() -> None:
    if sys.version_info < (3, 11):
        sys.exit(f"Python 3.11 or newer is required (found {sys.version.split()[0]}). Get it from https://python.org")


def ensure_venv() -> None:
    if venv_python().exists():
        return
    say("> Creating virtual environment (.venv)...")
    venv.create(VENV, with_pip=True)


def ensure_dependencies() -> None:
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()
    if STAMP.exists() and STAMP.read_text().strip() == digest:
        return
    say("> Installing dependencies (first run takes a minute)...")
    subprocess.run(
        [str(venv_python()), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQUIREMENTS)],
        check=True,
    )
    STAMP.write_text(digest)


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def try_key(provider: str, key: str, model: str) -> str:
    """Send a one-token request. Returns "ok", "rejected", "no_model" or "unverified"."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 1}).encode()
    request = urllib.request.Request(
        PROVIDERS[provider]["chat_url"],
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60):
            return "ok"
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return "rejected"
        if exc.code == 400:
            # Google reports a bad key as 400 API_KEY_INVALID; anything else is fine for a 1-token probe.
            return "rejected" if b"API_KEY" in exc.read() else "ok"
        if exc.code in (404, 410):
            return "no_model"
        return "unverified"  # 429 / 5xx: provider busy, key probably fine
    except (urllib.error.URLError, TimeoutError):
        return "unverified"


def ensure_api_key() -> None:
    env = {**read_env(), **{k: v for k, v in os.environ.items() if k.startswith("LLM_")}}
    if env.get("LLM_API_KEY"):
        return
    if not sys.stdin.isatty():
        sys.exit("No LLM_API_KEY found. Run `python start.py` in a terminal, or copy .env.example to .env and add a key.")

    say()
    say("This app needs a free API key for its language model (one-time setup).")
    say("  1) NVIDIA NIM        (default)")
    say("  2) Google AI Studio")
    provider = "gemini" if ask("Choose 1 or 2 [1]: ").strip() == "2" else "nvidia"
    info = PROVIDERS[provider]
    say(f"\nGet a {info['label']} key here: {info['key_url']}")

    while True:
        key = ask("Paste your API key: ").strip().strip('"').strip("'")
        if not key:
            continue
        say("  Checking the key (can take a few seconds)...")
        model, result = info["models"][0], "no_model"
        for model in info["models"]:
            result = try_key(provider, key, model)
            if result != "no_model":
                break
        if result == "rejected":
            say("  That key was rejected. Please check it and try again.")
            continue
        if result == "unverified":
            say(f"  {info['label']} is busy or unreachable right now, so the key couldn't be verified. Saving it anyway.")
        elif result == "no_model":
            say("  None of the tested models are available to this key; set LLM_MODEL in .env (see README).")
        break

    lines = [
        "# Written by start.py. This file is git-ignored; never commit real keys.",
        f"LLM_PROVIDER={provider}",
        f"LLM_API_KEY={key}",
        f"LLM_MODEL={model}",
    ]
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    say(f"  OK: saved to .env (provider: {info['label']}, model: {model})")


def run_server(port: int, open_browser: bool) -> int:
    url = f"http://localhost:{port}"
    cmd = [str(venv_python()), "-m", "uvicorn", "app.main:app", "--port", str(port)]
    say(f"\n> Starting the app at {url}  (press Ctrl+C to stop)\n")
    server = subprocess.Popen(cmd, cwd=ROOT)
    try:
        if open_browser:
            for _ in range(60):
                if server.poll() is not None:
                    break
                try:
                    urllib.request.urlopen(f"{url}/api/health", timeout=1)
                    webbrowser.open(url)
                    break
                except OSError:
                    time.sleep(0.5)
        return server.wait()
    except KeyboardInterrupt:
        server.terminate()
        server.wait()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Set up and run the Document Intake Assistant.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args()

    os.chdir(ROOT)
    check_python()
    ensure_venv()
    ensure_dependencies()
    ensure_api_key()
    return run_server(args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
