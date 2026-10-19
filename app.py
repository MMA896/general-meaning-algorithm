import os, re, sqlite3, secrets, json, urllib.request, hashlib, threading, time
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, g

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE, "research.db"))
CORPUS_PATH = os.path.join(BASE, "data", "quranic-corpus-morphology-0.4.txt")
CORPUS_URL = "https://raw.githubusercontent.com/bnjasim/quranic-corpus/master/quranic-corpus-morphology-0.4.txt"
VERSION = "2.1.4"
DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1")
USER = os.environ.get("RESEARCH_USER", "researcher")
PASSWORD = os.environ.get("RESEARCH_PASSWORD", "change-me")

# Shared cross-worker status/lock files prevent multiple Gunicorn workers from
# downloading and rebuilding the same corpus at the same time.
IMPORT_LOCK_PATH = os.environ.get("INDEX_LOCK_PATH", os.path.join(BASE, ".index_import.lock"))
IMPORT_STATUS_PATH = os.environ.get("INDEX_STATUS_PATH", os.path.join(BASE, ".index_import_status.json"))
_IMPORT_THREAD = None
_IMPORT_THREAD_GUARD = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_import_status():
    try:
        with open(IMPORT_STATUS_PATH, "r", encoding="utf-8") as f:
            status = json.load(f)
        if isinstance(status, dict):
            return status
    except (OSError, ValueError, TypeError):
        pass
    return {"state": "idle"}


def _write_import_status(state, **details):
    payload = {"state": state, "updated_at": _now_iso(), **details}
    try:
        parent = os.path.dirname(IMPORT_STATUS_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        temp = f"{IMPORT_STATUS_PATH}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(temp, IMPORT_STATUS_PATH)
    except OSError:
        # Status telemetry must never stop indexing/search itself.
        pass
    return payload


def _status_age_seconds(status):
    """Return status age, or None if its timestamp cannot be parsed."""
    raw = status.get("updated_at") or status.get("started_at")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())
    except (TypeError, ValueError):
        return None


def _pid_is_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (ProcessLookupError, ValueError, OSError, TypeError):
        return False


def _recover_stale_import_status(status=None):
    """Recover an abandoned 'starting/running' flag instead of freezing indexing forever.

    The status and lock live on the service filesystem, while a worker/thread may
    be restarted independently. A stale status must not be treated as proof that
    a real importer is still running.
    """
    status = dict(status or _read_import_status())
    state = status.get("state")
    if state not in {"starting", "running"}:
        return status

    age = _status_age_seconds(status)
    limit = 60 if state == "starting" else 600
    if age is not None and age <= limit:
