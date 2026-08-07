"""Tests for plugins.platforms.briar.adapter.

Run from repo root with pytest targeting this file.
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from plugins.platforms.briar.adapter import (
    BriarAdapter,
    _env_enablement,
    check_requirements,
    validate_config,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, *, status_resp=None, messages_resp=None, send_resp=None):
        self.status_resp = status_resp or FakeResponse(status=200)
        self.messages_resp = messages_resp or FakeResponse(status=200, payload=[])
        self.send_resp = send_resp or FakeResponse(status=200, payload={"id": "m1"})
        self.calls = []
        self.closed = False

    async def get(self, url, params=None, timeout=None):
        self.calls.append(("get", url, params))
        if url.endswith("/status"):
            return self.status_resp
        return self.messages_resp

    async def post(self, url, json=None, timeout=None):
        self.calls.append(("post", url, json))
        return self.send_resp

    async def close(self):
        self.closed = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def make_config(extra=None):
    return MagicMock(extra=extra or {})


# ---------------------------------------------------------------------------
# Config / env helpers
# ---------------------------------------------------------------------------

class TestRequirements:
    def test_returns_false_when_api_url_missing(self, monkeypatch):
        monkeypatch.delenv("BRIAR_API_URL", raising=False)
        monkeypatch.setenv("BRIAR_CONTACT_ID", "cid")
        assert check_requirements() is False

    def test_returns_false_when_contact_id_missing(self, monkeypatch):
        monkeypatch.setenv("BRIAR_API_URL", "http://127.0.0.1:7000")
        monkeypatch.delenv("BRIAR_CONTACT_ID", raising=False)
        assert check_requirements() is False

    def test_returns_true_when_both_set(self, monkeypatch):
        monkeypatch.setenv("BRIAR_API_URL", "http://127.0.0.1:7000")
        monkeypatch.setenv("BRIAR_CONTACT_ID", "cid")
        assert check_requirements() is True


class TestValidateConfig:
    def test_validates_from_env(self, monkeypatch):
        monkeypatch.setenv("BRIAR_API_URL", "http://127.0.0.1:7000")
        monkeypatch.setenv("BRIAR_CONTACT_ID", "cid")
        assert validate_config(make_config()) is True

    def test_validates_from_extra(self):
        cfg = make_config(extra={"api_url": "http://x", "contact_id": "cid"})
        assert validate_config(cfg) is True

    def test_rejects_missing_fields(self):
        assert validate_config(make_config()) is False


class TestEnvEnablement:
    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("BRIAR_API_URL", raising=False)
        monkeypatch.delenv("BRIAR_CONTACT_ID", raising=False)
        assert _env_enablement() is None

    def test_seeds_extra_and_home_channel(self, monkeypatch):
        monkeypatch.setenv("BRIAR_API_URL", "http://127.0.0.1:7000")
        monkeypatch.setenv("BRIAR_CONTACT_ID", "cid")
        monkeypatch.setenv("BRIAR_HOME_CHANNEL", "home-cid")
        out = _env_enablement()
        assert out["api_url"] == "http://127.0.0.1:7000"
        assert out["contact_id"] == "cid"
        assert out["home_channel"] == {"chat_id": "home-cid", "name": "Home"}


# ---------------------------------------------------------------------------
# Adapter lifecycle
# ---------------------------------------------------------------------------

class TestBriarAdapterLifecycle:
    def test_init_reads_env_and_defaults(self, monkeypatch):
        monkeypatch.setenv("BRIAR_API_URL", "http://127.0.0.1:7000")
        monkeypatch.setenv("BRIAR_CONTACT_ID", "cid")
        monkeypatch.delenv("BRIAR_ALLOWED_USERS", raising=False)
        adapter = BriarAdapter(make_config())
        assert adapter.api_url == "http://127.0.0.1:7000"
        assert adapter.contact_id == "cid"
        assert adapter.allowed_users == []
        assert adapter._session is None
        assert adapter._poll_task is None

    def test_init_prefers_env_over_extra(self):
        cfg = make_config(extra={"api_url": "http://x", "contact_id": "old"})
        with patch.dict(os.environ, {"BRIAR_API_URL": "http://y", "BRIAR_CONTACT_ID": "new"}, clear=False):
            adapter = BriarAdapter(cfg)
            assert adapter.api_url == "http://y"
            assert adapter.contact_id == "new"

    def test_parse_allowed_users(self, monkeypatch):
        monkeypatch.setenv("BRIAR_ALLOWED_USERS", "a, b ,c")
        adapter = BriarAdapter(make_config())
        assert adapter.allowed_users == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_connect_marks_connected_and_starts_polling(self):
        session = FakeSession(status_resp=FakeResponse(status=200))
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
            result = await adapter.connect()
        assert result is True
        assert session.closed is False
        assert adapter._poll_task is not None
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_connect_returns_false_on_unreachable_bridge(self):
        session = FakeSession(status_resp=FakeResponse(status=500))
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
            result = await adapter.connect()
        assert result is False
        assert adapter._session is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_poll_and_closes_session(self):
        session = FakeSession(status_resp=FakeResponse(status=200))
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
            await adapter.connect()
            task = adapter._poll_task
            assert task is not None
        await adapter.disconnect()
        assert task.cancelled()
        assert session.closed is True


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

class TestBriarAdapterSend:
    @pytest.mark.asyncio
    async def test_send_success(self):
        session = FakeSession(send_resp=FakeResponse(status=200, payload={"id": "m1"}))
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
            adapter._session = session
            result = await adapter.send("cid", "hello", reply_to="m0")
        assert result.success is True
        assert result.message_id == "m1"
        assert session.calls[-1][0] == "post"
        assert session.calls[-1][2]["message"] == "hello"

    @pytest.mark.asyncio
    async def test_send_failure_when_not_connected(self):
        adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
        adapter._session = None
        result = await adapter.send("cid", "hello")
        assert result.success is False
        assert "not connected" in result.error

    @pytest.mark.asyncio
    async def test_send_failure_on_http_error(self):
        session = FakeSession(send_resp=FakeResponse(status=502, text="bad gateway"))
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "cid"}))
            adapter._session = session
            result = await adapter.send("cid", "hello")
        assert result.success is False
        assert "502" in result.error


# ---------------------------------------------------------------------------
# Inbound polling
# ---------------------------------------------------------------------------

class TestBriarAdapterInbound:
    @pytest.mark.asyncio
    async def test_dispatches_text_message(self):
        session = FakeSession(
            status_resp=FakeResponse(status=200),
            messages_resp=FakeResponse(status=200, payload=[
                {"contact_id": "peer", "message": "hi", "timestamp": 1}
            ]),
        )
        dispatch = AsyncMock()
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "me"}))
            adapter.dispatch = dispatch
            await adapter.connect()
            await asyncio.sleep(0)
        assert dispatch.call_count == 1
        event = dispatch.call_args[0][0]
        assert event.text == "hi"
        assert event.chat_id == "peer"
        assert event.sender_id == "peer"
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_allowed_users_filters_inbound(self):
        session = FakeSession(
            status_resp=FakeResponse(status=200),
            messages_resp=FakeResponse(status=200, payload=[
                {"contact_id": "allowed", "message": "ok", "timestamp": 1},
                {"contact_id": "blocked", "message": "nope", "timestamp": 2},
            ]),
        )
        dispatch = AsyncMock()
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "me"}))
            adapter.allowed_users = ["allowed"]
            adapter.dispatch = dispatch
            await adapter.connect()
            await asyncio.sleep(0)
        assert dispatch.call_count == 1
        event = dispatch.call_args[0][0]
        assert event.sender_id == "allowed"
        await adapter.disconnect()

    @pytest.mark.asyncio
    async def test_ignores_empty_message(self):
        session = FakeSession(
            status_resp=FakeResponse(status=200),
            messages_resp=FakeResponse(status=200, payload=[
                {"contact_id": "peer", "timestamp": 1},
            ]),
        )
        dispatch = AsyncMock()
        with patch("plugins.platforms.briar.adapter.aiohttp.ClientSession", return_value=session):
            adapter = BriarAdapter(make_config(extra={"api_url": "http://127.0.0.1:7000", "contact_id": "me"}))
            adapter.dispatch = dispatch
            await adapter.connect()
            await asyncio.sleep(0)
        assert dispatch.call_count == 0
        await adapter.disconnect()
