# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Activation, the public route contract, and async choices."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fake_host import FakeHost, FakeSettings
from ollama import activate
from ollama.tasks import CHAT, FIXES, IMAGE_DESCRIPTION, TASK_LABELS, TASKS, TRANSLATION
from slidemage.plugin_api import Route
from ollama.provider import DEFAULT_OLLAMA_HOST, InstalledModel, OllamaProvider, ollama_host
from ollama.route import OllamaRoute


def test_activation_is_lazy_and_needs_no_qt_or_network(monkeypatch):
    subprocess.run([sys.executable, "-c", """
import socket, sys
sys.modules['PySide6'] = None
def no_network(*args, **kwargs):
    raise AssertionError('activation attempted network')
socket.socket.connect = no_network
from fake_host import FakeHost
from ollama import activate
host = FakeHost()
activate(host)
assert not host.pending
assert 'ollama.settings' not in sys.modules
"""], check=True, env={**os.environ, "PYTHONPATH": os.pathsep.join((".", "tests", os.environ.get("PYTHONPATH", "")))})
    host = FakeHost()
    activate(host)
    host.finish_activation()
    route = host.routes["ollama"]
    section, = host.sections
    assert (section.page, section.title) == ("ai", "Ollama")
    calls, page = [], object()
    monkeypatch.setitem(sys.modules, "ollama.settings", SimpleNamespace(
        OllamaTab=lambda h, r: (calls.append((h, r)) or SimpleNamespace(page=page))))
    assert section.build() is page
    assert calls == [(host, route)]
    assert (route.id, route.service, route.is_local, route.tasks) == ("ollama", "Ollama", True, frozenset(TASKS))
    with pytest.raises(RuntimeError):
        host.add_ai_route(route)


def test_address_precedence_and_provider_captures_settings(monkeypatch):
    host = FakeHost()
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    assert ollama_host(host) == DEFAULT_OLLAMA_HOST
    monkeypatch.setenv("OLLAMA_HOST", "http://environment:11434")
    assert ollama_host(host) == "http://environment:11434"
    host.store.set("ollama_host", "http://saved:11434")
    provider = OllamaProvider.for_host(host, "chosen")
    host.store.set("ollama_host", "http://new:11434")
    assert (provider.host, provider.model) == ("http://saved:11434", "chosen")
    assert OllamaProvider.for_host(host).host == "http://new:11434"
    host.store.set("ollama_host", " ")
    assert ollama_host(host) == "http://environment:11434"


@pytest.mark.parametrize("task", TASKS)
def test_local_problem_text_and_no_fallback(task):
    route = OllamaRoute(FakeHost())
    expected = f"Choose an Ollama model for {TASK_LABELS[task].lower()} under AI setup (the ▾ of the AI switch)."
    missing = Route("ollama", task, "")
    resolved = Route("ollama", task, "qwen3:32b")
    assert route.default_choice(task) == "" and route.problem(None, missing) == expected
    assert route.problem(None, resolved) == ""
    assert route.provider(None, missing).model == ""
    assert route.provider(None, resolved).model == "qwen3:32b"
    assert route.provider(None, resolved) is not route.provider(None, resolved)
    assert route.choice_label(resolved.model) == resolved.model
    assert route.split_choice(resolved.model) == (resolved.model, None)
    assert route.valid_choice(None, resolved.model) and not route.valid_choice(None, "")
    assert route.work_label(resolved) == "qwen3:32b · Ollama"
    assert route.work_label(missing) == "no model chosen · Ollama"
    assert route.draft_backend(None, resolved, cancel=object()) is None
    assert route.translation_backend(None, resolved, cancel=object()) is None
    assert route.slide_translator(None, resolved) is None
    assert route.task_runner(None, resolved) is None
    assert route.chat_surface() is None and route.settings_section() is None


def test_model_choices_use_a_worker_and_filter_capabilities(monkeypatch):
    host = FakeHost()
    route = OllamaRoute(host)
    installed = [InstalledModel("qwen3", 1, ""), InstalledModel("eyes", 2, "", ("vision", "tools")),
                 InstalledModel("writer", 3, "", ("completion",))]
    calls = []

    def discover(provider, *, detailed):
        calls.append((provider.host, detailed))
        return installed

    monkeypatch.setattr(OllamaProvider, "installed", discover)
    assert all(route.model_choices(None, task) == [] for task in TASKS)
    assert len(host.pending) == 1 and calls == []
    host.finish_next()
    assert calls == [(ollama_host(host), True)]
    assert route.model_choices(None, CHAT) == ["qwen3", "eyes"]
    assert route.model_choices(None, IMAGE_DESCRIPTION) == ["eyes"]
    assert route.model_choices(None, TRANSLATION) == ["qwen3", "eyes", "writer"]
    assert route.model_choices(None, FIXES) == ["qwen3", "eyes", "writer"]
    assert not host.pending


def test_old_model_lists_cannot_replace_a_new_address(monkeypatch):
    host = FakeHost(settings=FakeSettings({"ollama_host": "http://old"}))
    route = OllamaRoute(host)
    monkeypatch.setattr(OllamaProvider, "installed", lambda p, **kw: [InstalledModel(p.host, 0, "")])
    route.model_choices(None, TRANSLATION)
    host.store.set("ollama_host", "http://new")
    assert route.model_choices(None, TRANSLATION) == []
    host.finish_next(index=1)  # the new address answers first
    host.finish_next()
    assert route.model_choices(None, TRANSLATION) == ["http://new"]


def test_discovery_errors_are_logged_and_can_be_refreshed(monkeypatch):
    host = FakeHost()
    route = OllamaRoute(host)

    def fail(*args, **kwargs):
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(OllamaProvider, "installed", fail)
    route.model_choices(None, CHAT)
    host.finish_next()
    assert route.model_choices(None, CHAT) == [] and not host.pending
    assert host.logs == ["Could not list Ollama models: discovery failed"]
    route.refresh_models()
    assert len(host.pending) == 1
