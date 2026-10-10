"""
tests/test_director_hardening.py
================================
Regression tests for the "frozen agent" bug and the deterministic router.

The original bug: Director's API-error handler was `while True: ... continue` with no
cap, so any deterministic 400/404 (e.g. thought_signature) retried forever.
"""

import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import Agentic_Core.director as director_mod
from Agentic_Core.director import AutonomousDirector, classify_api_error
from Agentic_Core.goal_router import classify_goal, detect_platform


# ─────────────────────────── helpers ────────────────────────────────────────
class _FakeChat:
    def __init__(self, behaviours):
        self.behaviours = behaviours   # list of Exception | response
        self.calls = 0

    def send_message(self, _inp):
        b = self.behaviours[min(self.calls, len(self.behaviours) - 1)]
        self.calls += 1
        if isinstance(b, Exception):
            raise b
        return b


def _make_director(chat_factory, monkeypatch):
    sleeps = []
    monkeypatch.setattr(director_mod.time, "sleep", lambda s: sleeps.append(s))
    d = AutonomousDirector(api_key="test_key")
    d.client = SimpleNamespace(chats=SimpleNamespace(create=lambda **kw: chat_factory()))
    return d, sleeps


def _text_response(text="done"):
    return SimpleNamespace(function_calls=None, text=text, candidates=[])


# ─────────────────────────── error classification ───────────────────────────
@pytest.mark.parametrize("msg,expected", [
    ("400 INVALID_ARGUMENT: Function call is missing a thought_signature", "signature"),
    ("429 RESOURCE_EXHAUSTED quota exceeded", "quota"),
    ("503 UNAVAILABLE model overloaded", "server"),
    ("Request timed out", "server"),
    ("404 NOT_FOUND: model no longer available", "model_gone"),
    ("API key not valid. Please pass a valid API key", "auth"),
    ("400 INVALID_ARGUMENT: bad schema", "fatal"),
])
def test_classify_api_error(msg, expected):
    assert classify_api_error(Exception(msg)) == expected


# ─────────────────────────── the freeze regression ───────────────────────────
def test_deterministic_400_never_loops(monkeypatch):
    chat = _FakeChat([Exception("400 INVALID_ARGUMENT: bad request schema")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel", max_turns=5)
    assert res["status"] == "error"
    assert chat.calls == 1 and sleeps == []          # fail fast, zero sleeping


def test_thought_signature_recovers_once_then_fails(monkeypatch):
    chats = []
    def factory():
        c = _FakeChat([Exception("400 INVALID_ARGUMENT thought_signature missing")])
        chats.append(c)
        return c
    d, sleeps = _make_director(factory, monkeypatch)
    res = d.run_goal("make a reel", max_turns=5)
    assert res["status"] == "error" and res["error_kind"] == "signature"
    assert len(chats) == 2                            # original + exactly one recovery chat
    assert sleeps == []


def test_model_gone_fails_fast(monkeypatch):
    chat = _FakeChat([Exception("404 NOT_FOUND model not found")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel")
    assert res["status"] == "error" and res["error_kind"] == "model_gone"
    assert chat.calls == 1


def test_transient_errors_are_bounded(monkeypatch):
    chat = _FakeChat([Exception("503 UNAVAILABLE overloaded")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel")
    assert res["status"] == "error" and res["error_kind"] == "server"
    assert chat.calls == director_mod._MAX_API_RETRIES + 1
    assert len(sleeps) == director_mod._MAX_API_RETRIES


def test_transient_error_then_success(monkeypatch):
    chat = _FakeChat([Exception("429 quota"), _text_response("all good")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel")
    assert res["status"] == "success" and res["final_summary"] == "all good"
    assert len(sleeps) == 1


def test_deadline_stops_retries(monkeypatch):
    monkeypatch.setattr(director_mod, "_DEADLINE_SEC", 1.0)
    chat = _FakeChat([Exception("429 quota")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel")
    assert res["status"] == "timeout"                 # 5s backoff does not fit in a 1s budget
    assert sleeps == []


def test_auth_error_fails_immediately(monkeypatch):
    chat = _FakeChat([Exception("API key not valid")])
    d, sleeps = _make_director(lambda: chat, monkeypatch)
    res = d.run_goal("make a reel")
    assert res["status"] == "error" and res["error_kind"] == "auth"
    assert chat.calls == 1


# ─────────────────────────── publish guard ──────────────────────────────────
def test_publish_blocked_when_not_requested(monkeypatch):
    fc = SimpleNamespace(name="tool_publish_clip", args={"video_path": "/tmp/x.mp4"})
    calls = {"n": 0}
    def side():
        calls["n"] += 1
        return SimpleNamespace(function_calls=[fc], text="", candidates=[]) if calls["n"] == 1 else _text_response("ok")
    chat = SimpleNamespace(send_message=lambda _i: side())
    d, _ = _make_director(lambda: chat, monkeypatch)
    published = {"called": False}
    monkeypatch.setitem(director_mod.TOOL_DISPATCH_MAP, "tool_publish_clip",
                        lambda **kw: published.update(called=True) or {"status": "success"})
    res = d.run_goal("edit this reel and audit it")   # no "publish" in goal
    assert published["called"] is False
    assert res["execution_log"][0]["result_status"] == "aborted"


# ─────────────────────────── router ─────────────────────────────────────────
def test_router_url_direct():
    r = classify_goal("https://www.instagram.com/reel/ABC123/")
    assert r.kind == "url" and r.platform == "instagram" and r.directive == "" and r.is_direct


def test_router_url_with_instructions_becomes_directive():
    r = classify_goal("https://youtu.be/xyz make the cuts faster and change the music")
    assert r.kind == "url" and r.platform == "youtube"
    assert "faster" in r.directive and "http" not in r.directive


def test_router_handle_forms():
    assert classify_goal("@nike").handles == ["nike"]
    assert classify_goal("scrape: nike").kind == "handle"
    assert classify_goal("hello there").kind == "freeform"      # bare words are NOT handles
    assert classify_goal("faster").kind == "freeform"


def test_router_reedit_preset_and_custom():
    r = classify_goal("Re-edit session 'sess_1777_ab' with directive: 'Change Music & Beat Alignment — faster'")
    assert r.kind == "reedit" and r.session_id == "sess_1777_ab"
    assert r.directive.startswith("Change Music")


def test_router_batch_and_pool():
    r = classify_goal("Ingest reels for creator accounts: @a, @b, @c, edit each with beat-synced rhythm and BGM, and audit quality.")
    assert r.kind == "batch" and r.handles == ["a", "b"]          # capped at 2 (anti-duplicate rotation policy)
    assert classify_goal("Run scheduled content scraper batch for active creator accounts, render reels, and audit.").scheduled_pool


def test_router_upload():
    r = classify_goal("Process uploaded video at '/tmp/dl/telegram_1/video.mp4', edit with beat-synced rhythm and BGM, audit quality, and publish to social platforms.")
    assert r.kind == "upload" and r.path == "/tmp/dl/telegram_1/video.mp4"


def test_router_freeform_goes_to_llm():
    r = classify_goal("make my last reel feel more cinematic and punchy")
    assert r.kind == "freeform" and not r.is_direct


def test_detect_platform():
    assert detect_platform("https://www.tiktok.com/@x/video/1") == "tiktok"
    assert detect_platform("https://www.youtube.com/shorts/abc") == "youtube"
    assert detect_platform("https://instagram.com/reel/x") == "instagram"


# ─────────────────────────── tool gaps ──────────────────────────────────────
def test_ingest_local_upload_is_not_scraped(tmp_path):
    from Agentic_Core.tool_adapters import tool_ingest_source
    vid = tmp_path / "video.mp4"
    vid.write_bytes(b"\x00" * 2048)
    with patch("Phase_1.phase1_orchestrator.run_phase1_pipeline") as p1:
        res = tool_ingest_source(str(vid))
        p1.assert_not_called()                                    # previously: bogus Apify scrape on a file path
    assert res["status"] == "success" and res["clip_dir"] == str(tmp_path)


def test_phase3_platform_normalization():
    from Phase_3.phase3_orchestrator import normalize_platforms
    assert normalize_platforms(None) is None
    assert normalize_platforms([]) is None
    assert normalize_platforms(["Instagram", "shorts"]) == {"meta", "youtube"}
    assert normalize_platforms(["myspace"]) is None
