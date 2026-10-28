# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""OllamaProvider against a throwaway local HTTP server (no external network): request shape, response
parsing, error mapping, abort, capabilities. The server is the `server` fixture (conftest.py)."""
from __future__ import annotations

import base64
import threading

import pytest

from ollama.provider import OllamaProvider
from slidemage.ai.providers import Cancelled, ProviderError   # neutral: moves to slidemage.plugin_api (step 5b)

PNG = b"\x89PNG\r\n\x1a\n" + b"fake"


# -- Ollama ------------------------------------------------------------------

def test_ollama_chat_request_shape_and_parsing(server):
    server.body = {"message": {"content": "answer", "thinking": "hm"}, "done_reason": "length"}
    provider = OllamaProvider("qwen3", host=server.url)
    reply = provider.chat(
        [{"role": "user", "content": "hi"}], system="be brief", images=[PNG],
        schema={"type": "object"}, temperature=0.1, max_tokens=99, num_ctx=4096)
    request = server.requests[0]
    assert (request["method"], request["path"]) == ("POST", "/api/chat")
    assert request["body"] == {
        "model": "qwen3", "stream": False, "format": {"type": "object"},
        "options": {"temperature": 0.1, "num_predict": 99, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi", "images": [base64.b64encode(PNG).decode()]}]}
    assert (reply.text, reply.thinking, reply.truncated) == ("answer", "hm", True)


def test_ollama_sends_think_only_when_enabled(server):
    server.body = {"message": {"content": "x"}}
    provider = OllamaProvider("m", host=server.url)
    provider.chat([{"role": "user", "content": "a"}])
    provider.chat([{"role": "user", "content": "a"}], think=True)
    assert "think" not in server.requests[0]["body"]
    assert server.requests[1]["body"]["think"] is True


def test_ollama_errors_become_provider_errors(server):
    provider = OllamaProvider("m", host=server.url)
    server.status, server.body = 500, {"error": "model crashed"}
    with pytest.raises(ProviderError, match="Ollama returned HTTP 500: model crashed"):
        provider.chat([{"role": "user", "content": "a"}])
    dead = OllamaProvider("m", host="http://127.0.0.1:9")
    with pytest.raises(ProviderError, match="Ollama unreachable"):
        dead.chat([{"role": "user", "content": "a"}])


def test_ollama_lists_models_newest_first_and_filters_vision(server):
    server.body = {"models": [
        {"name": "old", "modified_at": "2026-01-01", "size": 5, "capabilities": ["vision"]},
        {"name": "new", "modified_at": "2026-06-01", "size": 7},
    ]}
    provider = OllamaProvider(host=server.url)
    assert provider.list_models() == ["new", "old"]
    assert provider.list_models(vision_only=True) == ["old"]
    assert [m.size_bytes for m in provider.installed()] == [7, 5]
    assert OllamaProvider(host="http://127.0.0.1:9").list_models() == []


def test_abort_ends_a_running_request(server):
    server.hold = True
    provider = OllamaProvider("m", host=server.url)
    outcome = []

    def run():
        try:
            provider.chat([{"role": "user", "content": "a"}])
        except ProviderError as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    while not server.requests:
        thread.join(0.01)
    provider.abort()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(outcome[0], Cancelled)


def test_the_socket_is_known_before_the_body_goes_out_and_a_reset_is_not_unreachable(monkeypatch):
    marker = object()
    seen = []

    class Connection:
        fail = None

        def __init__(self, *_args, **_kwargs):
            self.sock = None

        def connect(self):
            if self.fail == "connect":
                raise ConnectionRefusedError("refused")
            self.sock = marker

        def request(self, *_args, **_kwargs):
            seen.append(provider._sock)             # what abort() would shut down while the upload runs
            raise ConnectionResetError("reset by peer")

        def close(self):
            pass

    monkeypatch.setattr("slidemage.ai.providers.http.client.HTTPConnection", Connection)
    provider = OllamaProvider("m", host="http://ollama.test")
    with pytest.raises(ProviderError, match="dropped the connection"):
        provider.chat([{"role": "user", "content": "a"}])
    assert seen == [marker] and provider._sock is None
    Connection.fail = "connect"
    with pytest.raises(ProviderError, match="Ollama unreachable"):
        OllamaProvider("m", host="http://ollama.test").chat([{"role": "user", "content": "a"}])


def test_capabilities_are_asked_once_per_model(server):
    # Regression (1c7e047): cache successful capabilities, but retry unknown models.
    server.body = {"capabilities": ["completion", "vision"]}
    provider = OllamaProvider("m", host=server.url)
    assert provider.capabilities() == ("completion", "vision") and provider.capabilities() == ("completion", "vision")
    assert len([r for r in server.requests if r["path"] == "/api/show"]) == 1
    assert server.requests[0]["body"] == {"model": "m"}
    server.body = {}
    assert provider.capabilities("other") == () and provider.capabilities("other") == ()      # not kept: asked again
    assert len([r for r in server.requests if r["path"] == "/api/show"]) == 3
    server.status, server.body = 404, {"error": "model 'm' not found"}
    assert OllamaProvider("m", host=server.url).capabilities() == ()
    assert OllamaProvider("m", host="http://127.0.0.1:9").capabilities() == ()
