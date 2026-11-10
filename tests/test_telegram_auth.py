import asyncio
import json
import os
from datetime import timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from discovery.policy import now_utc
from discovery.store import LeadStore
from discovery.telegram import fetch_mtproto
from discovery.telegram_auth import enable_adapter, load_auth, save_auth


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI store")
def test_encrypted_auth_roundtrip(tmp_path):
    path = tmp_path / "telegram.auth"
    save_auth(123456, "a" * 32, "test-session-material", path)
    encrypted = path.read_bytes()
    assert b"test-session-material" not in encrypted
    assert b"a" * 32 not in encrypted
    assert load_auth(path) == {"version": 1, "api_id": 123456, "api_hash": "a" * 32, "session": "test-session-material"}


def test_enabling_adapter_preserves_other_settings(tmp_path):
    path = tmp_path / "sources.json"
    original = {"telegram": {"adapter": "preview", "channels": [{"handle": "example"}]}, "other": [1, 2]}
    path.write_text(json.dumps(original), encoding="utf-8")
    enable_adapter(path)
    original["telegram"]["adapter"] = "mtproto"
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_authenticated_reader_works_in_server_worker_thread(tmp_path, monkeypatch):
    import discovery.telegram_auth as auth
    calls = []
    stamp = now_utc()
    class Client:
        async def connect(self):
            calls.append("connect")
        async def is_user_authorized(self):
            return True
        async def disconnect(self):
            calls.append("disconnect")
        async def iter_messages(self, channel, limit):
            assert channel == "example" and limit == 2000
            yield SimpleNamespace(id=2, date=stamp, message="Power Apps job", entities=[SimpleNamespace(url="https://jobs.example.com/1")], buttons=[], edit_date=stamp, media=None)
            yield SimpleNamespace(id=1, date=stamp - timedelta(days=8), message="old", entities=[], buttons=[], edit_date=None, media=None)
    def make_client():
        # Factory must run inside a live event loop, unlike the earlier sync reader.
        assert asyncio.get_running_loop()
        return Client()
    monkeypatch.setattr(auth, "make_client", make_client)
    with ThreadPoolExecutor(max_workers=1) as executor:
        posts, report = executor.submit(fetch_mtproto, "example", LeadStore(tmp_path / "leads.db")).result()
    assert len(posts) == 1 and report["complete"]
    assert posts[0]["links"] == ["https://jobs.example.com/1"]
    assert calls == ["connect", "disconnect"]


def test_unauthorized_session_does_not_prompt_or_read(tmp_path, monkeypatch):
    import discovery.telegram_auth as auth
    states = []
    class Client:
        async def connect(self):
            pass
        async def is_user_authorized(self):
            return False
        async def disconnect(self):
            states.append("disconnected")
    monkeypatch.setattr(auth, "make_client", lambda: Client())
    monkeypatch.setattr(auth, "write_status", lambda state, **kw: states.append(state))
    with pytest.raises(RuntimeError, match="needs login"):
        fetch_mtproto("example", LeadStore(tmp_path / "leads.db"))
    assert states == ["needs_login", "disconnected"]
