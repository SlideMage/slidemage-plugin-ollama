# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The plugin-side draft contract: activation, migration, routing and async choices."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from fake_host import FakeHost, FakeSettings
from ollama import MIGRATED_KEY, activate
from ollama.host import CHAT, FIXES, IMAGE_DESCRIPTION, TASK_LABELS, TASKS, TRANSLATION, ProviderError
from ollama.provider import DEFAULT_OLLAMA_HOST, InstalledModel, OllamaProvider, ollama_host
from ollama.route import OllamaRoute


def test_activation_is_lazy_and_needs_no_qt_slidemage_or_network(monkeypatch):
    subprocess.run([sys.executable, "-c", """
import socket, sys
sys.modules['PySide6'] = sys.modules['slidemage'] = None
def no_network(*args, **kwargs):
    raise AssertionError('activation attempted network')
socket.socket.connect = no_network
from fake_host import FakeHost
from ollama import activate
host = FakeHost()
activate(host)
assert not host.pending
assert 'ollama.settings' not in sys.modules
"""], check=True, env={**os.environ, "PYTHONPATH": os.pathsep.join((".", "tests"))})
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


def test_migration_reads_old_keys_once_and_preserves_plugin_choices():
    store = FakeSettings({"ollama_host": "", "models": {CHAT: "my-chat", TRANSLATION: ""}})
    legacy = {"ollama_host": "http://old:11434", "ai_models": {
        "local": {CHAT: "old-chat", IMAGE_DESCRIPTION: "vision", TRANSLATION: "old-translate",
                  FIXES: "writer", "unknown": "ignore"},
        "api": {CHAT: "never-use-cloud"}}}
    host = FakeHost(settings=store, legacy=legacy)
    activate(host)
    assert host.legacy_reads == ["ollama_host", "ai_models"]
    assert store.saved == {"ollama_host": "", "models": {
        CHAT: "my-chat", IMAGE_DESCRIPTION: "vision", TRANSLATION: "", FIXES: "writer"}, MIGRATED_KEY: True}
    assert host.legacy == legacy  # no writes or aliases into SlideMage's settings
    again = FakeHost(settings=FakeSettings(store.saved), legacy={"ollama_host": "http://changed"})
    activate(again)
    assert again.legacy_reads == [] and again.store.save_count == 0
    assert again.routes["ollama"].default_choice(CHAT) == "my-chat"


def test_old_address_and_all_four_models_are_migrated():
    host = FakeHost(legacy={"ollama_host": " http://other:1234 ", "ai_models": {
        "local": {task: f"model-{task}" for task in TASKS}}})
    activate(host)
    assert ollama_host(host) == "http://other:1234"
    route = host.routes["ollama"]
    for task in TASKS:
        assert route.default_choice(task) == f"model-{task}"
        assert route.problem(None, task) == ""


@pytest.mark.parametrize("legacy", [
    {}, {"ollama_host": 4, "ai_models": []}, {"ai_models": {"local": "bad"}},
    {"ai_models": {"local": {CHAT: None, TRANSLATION: [], FIXES: 5}}},
])
def test_empty_or_malformed_legacy_settings_are_not_choices(legacy):
    host = FakeHost(legacy=legacy)
    activate(host)
    assert host.store.saved == {MIGRATED_KEY: True}
    assert all(host.routes["ollama"].default_choice(task) == "" for task in TASKS)


def test_failed_migration_save_is_reported_and_retried():
    store = FakeSettings()
    store.save_error = OSError("disk full")
    host = FakeHost(settings=store, legacy={"ollama_host": "http://old"})
    activate(host)
    assert not store.get(MIGRATED_KEY)
    assert host.messages == [("Could not save Ollama settings: disk full", True)]
    assert "ollama" in host.routes
    store.save_error = None
    again = FakeHost(settings=store)
    activate(again)
    assert again.legacy_reads == ["ollama_host", "ai_models"]
    assert store.saved["ollama_host"] == "http://old" and store.saved[MIGRATED_KEY]


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
    assert route.default_choice(task) == "" and route.problem(None, task) == expected
    with pytest.raises(ProviderError) as caught:
        route.provider(None, SimpleNamespace(task=task, model=""))
    assert str(caught.value) == expected
    # A picker's explicit choice works even when there is no saved default.
    resolved = SimpleNamespace(task=task, model="explicit")
    assert route.provider(None, resolved).model == "explicit"
    assert route.provider(None, resolved) is not route.provider(None, resolved)
    assert route.choice_label("qwen3:32b") == "qwen3:32b"
    assert all(getattr(route, name)(None, resolved) is None for name in (
        "draft_backend", "translation_backend", "slide_translator", "task_runner"))
    assert route.chat_surface() is None


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
