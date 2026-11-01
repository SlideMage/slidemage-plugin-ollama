# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""pull_model's progress reports: structured, and not repeated for every streamed event."""
from __future__ import annotations

import json

import pytest

from fake_http import Server
from ollama.admin import Progress, pull_model


def _stream(*events: dict) -> str:
    return "".join(json.dumps(event) + "\n" for event in events)


@pytest.fixture
def server(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    try:
        server = Server()
    except PermissionError:
        pytest.skip("sandbox forbids loopback sockets")
    yield server
    server.close()


def test_pull_reports_progress_without_repeats(server):
    layer = {"status": "pulling abc", "total": 1000}
    server.body = _stream(
        {"status": "pulling manifest"}, {"status": "pulling manifest"},
        {**layer, "completed": 1}, {**layer, "completed": 2}, {**layer, "completed": 500},
        {**layer, "completed": 504}, {**layer, "completed": 1000}, {"status": "success"})
    reports: list[Progress] = []
    assert pull_model("qwen3", server.url, on_progress=reports.append) == (True, "")
    assert reports == [
        Progress("qwen3: pulling manifest"),
        Progress("qwen3: pulling abc (0%)", 0.0),
        Progress("qwen3: pulling abc (50%)", 0.5),
        Progress("qwen3: pulling abc (100%)", 1.0),
        Progress("qwen3: success")]


def test_pull_error_event_fails(server):
    server.body = _stream({"status": "pulling manifest"}, {"error": "no such model"})
    reports: list[Progress] = []
    assert pull_model("nope", server.url, on_progress=reports.append) == (False, "no such model")
    assert reports == [Progress("nope: pulling manifest")]
