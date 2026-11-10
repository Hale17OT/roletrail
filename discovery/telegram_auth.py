"""Windows user-bound encryption for Telegram API credentials and StringSession.

No phone number, login code, or two-step password is persisted. The session is an
account credential; Roletrail only uses it to read its configured job channels.
"""
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

from .store import STATE_DIR

AUTH_FILE = STATE_DIR / "telegram.auth"
STATUS_FILE = STATE_DIR / "telegram_connection_status.json"


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("The protected Telegram credential store requires Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                          ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    operation.restype = wintypes.BOOL
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(target.data, ctypes.c_void_p))


def save_auth(api_id, api_hash, session, path=None):
    path = Path(path or AUTH_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "api_id": int(api_id), "api_hash": api_hash, "session": session}
    encrypted = _crypt(json.dumps(payload).encode("utf-8"))
    temporary = path.with_suffix(".auth.tmp")
    temporary.write_bytes(encrypted)
    os.replace(temporary, path)


def load_auth(path=None):
    path = Path(path or AUTH_FILE)
    if not path.exists():
        return None
    data = json.loads(_crypt(path.read_bytes(), decrypt=True))
    if not data.get("api_id") or not data.get("api_hash") or not data.get("session"):
        raise ValueError("Incomplete protected Telegram session")
    return data


def make_client():
    """Create inside an async context; also works in threaded HTTP workers."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    saved = load_auth()
    if saved:
        return TelegramClient(StringSession(saved["session"]), saved["api_id"], saved["api_hash"],
                              flood_sleep_threshold=0, receive_updates=False)
    api_id, api_hash = os.environ.get("TELEGRAM_API_ID"), os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("Telegram is not connected. Run: python connect_telegram.py")
    return TelegramClient(os.environ.get("TELEGRAM_SESSION", str(STATE_DIR / "telegram")),
                          int(api_id), api_hash, flood_sleep_threshold=0, receive_updates=False)


def connection_status():
    # This is intentionally safe to display without returning credential material.
    return json.loads(STATUS_FILE.read_text(encoding="utf-8")) if STATUS_FILE.exists() else {"state": "not_connected"}


def write_status(state, **fields):
    from .policy import now_utc
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {"state": state, "updatedAt": now_utc().isoformat(), **fields}
    temporary = STATUS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temporary, STATUS_FILE)


def enable_adapter(config_path=None):
    path = Path(config_path or Path(__file__).with_name("sources.json"))
    config = json.loads(path.read_text(encoding="utf-8"))
    config["telegram"]["adapter"] = "mtproto"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)
