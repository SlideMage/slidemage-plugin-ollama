# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Qt widgets with a queued host: worker boundaries, task slots and page lifetime."""
from __future__ import annotations

import gc
import weakref

import pytest

from fake_host import FakeHost, FakeSettings
from ollama.host import TRANSLATION
from ollama.provider import InstalledModel, OllamaProvider
from ollama.route import OllamaRoute


@pytest.fixture(scope="module")
def qapp():
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture
def tab(qapp, monkeypatch):
    from ollama.settings import OllamaTab

    monkeypatch.setattr(OllamaProvider, "installed", lambda *_a, **_k: [InstalledModel("qwen3", 1024, "")])
    host = FakeHost(settings=FakeSettings({"ollama_host": "http://original:11434"}))
    page = OllamaTab(host, OllamaRoute(host))
    host.finish_next()
    page.models_list.item(0).setSelected(True)
    yield page
    if page._alive:
        page.page.deleteLater()
        _destroy_pages()


def _destroy_pages():
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_settings_section_builds_and_discovers_lazily(qapp, monkeypatch):
    monkeypatch.setattr(OllamaProvider, "installed", lambda *_a, **_k: [])
    host = FakeHost()
    route = OllamaRoute(host)
    page = route.settings_section()
    assert len(host.pending) == 1
    page.deleteLater()
    _destroy_pages()
    host.finish_next()  # late discovery must not touch any deleted widgets


def test_page_retains_controller_and_callbacks_after_discovery(qapp, monkeypatch):
    from PySide6.QtWidgets import QLineEdit
    from ollama.settings import OllamaTab

    monkeypatch.setattr(OllamaProvider, "installed", lambda *_a, **_k: [])
    host = FakeHost()
    route = OllamaRoute(host)
    page = route.settings_section()
    controller = weakref.ref(page.findChild(OllamaTab))
    host.finish_next()
    gc.collect()
    assert controller() is not None
    field = page.findChild(QLineEdit)
    field.setText("http://after-discovery")
    field.editingFinished.emit()
    assert host.store.saved["ollama_host"] == "http://after-discovery"
    page.deleteLater()
    _destroy_pages()
    host.finish_next()


def test_address_save_clear_and_model_refresh(tab, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://environment:11434")
    assert tab.update_button.isEnabled() and tab.delete_button.isEnabled()
    tab.host_edit.setText("  http://new:1234  ")
    tab._on_host()
    assert tab.host.store.saved["ollama_host"] == "http://new:1234"
    assert not tab.models_list.isEnabled()
    tab.host.finish_next()
    assert tab.models_list.isEnabled()
    tab.host_edit.setText(" ")
    tab._on_host()
    assert tab.host.store.saved["ollama_host"] == ""
    assert tab.host_edit.text() == "http://environment:11434"
    tab.host.finish_next()


def test_failed_address_save_keeps_previous_value(tab):
    tab.host.store.save_error = OSError("disk full")
    tab.host_edit.setText("http://unsaved")
    tab._on_host()
    assert tab.host.store.get("ollama_host") == "http://original:11434"
    assert tab.host_edit.text() == "http://original:11434"
    assert tab.host.messages == [("Could not save Ollama address: disk full", True)]
    assert not tab.host.pending


@pytest.mark.parametrize("operation", ["delete", "update"])
def test_busy_slot_prevents_administration(tab, monkeypatch, operation):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    tab.host.task = "a drafting batch"
    getattr(tab, f"_on_{operation}")()
    assert not tab.host.pending and tab.host.task_ends == 0
    assert tab.host.task == "a drafting batch"


@pytest.mark.parametrize("operation", ["delete", "update"])
def test_cancelled_confirmation_leaves_no_slot(tab, monkeypatch, operation):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.No)
    getattr(tab, f"_on_{operation}")()
    assert tab.host.task is None and not tab.host.pending
    assert tab.host.task_ends == (1 if operation == "delete" else 0)


@pytest.mark.parametrize("operation", ["delete", "update"])
@pytest.mark.parametrize("outcome", ["success", "failure", "exception"])
def test_admin_finishes_after_page_is_destroyed(tab, monkeypatch, operation, outcome):
    from PySide6.QtWidgets import QMessageBox
    from ollama import admin

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    calls = []

    def work(model, address, **kwargs):
        calls.append((model, address))
        if "on_progress" in kwargs:
            kwargs["on_progress"]("pulling weights")
        if outcome == "exception":
            raise ValueError("broken reply")
        return outcome == "success", "" if outcome == "success" else "server refused"

    monkeypatch.setattr(admin, f"{operation}_model", work)
    getattr(tab, f"_on_{operation}")()
    assert tab.host.task is not None and len(tab.host.pending) == 1
    assert not tab.host_edit.isEnabled() and not tab.update_button.isEnabled()
    tab.host.store.set("ollama_host", "http://changed-while-running")
    tab.page.deleteLater()
    _destroy_pages()
    assert not tab._alive
    tab.host.finish_next()
    assert calls == [("qwen3", "http://original:11434")]
    assert tab.host.task is None and tab.host.task_ends == 1
    assert bool(tab.host.messages) == (outcome != "success")
    if tab.host.messages:
        assert tab.host.messages[-1][1] is True
    tab.host.finish_next()  # refresh the route's cache, without the page
    assert tab.route.model_choices(None, TRANSLATION) == ["qwen3"]


def test_scheduling_failure_releases_slot_and_reports_error(tab, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    tab.host.background_error = RuntimeError("no worker available")
    tab._on_delete()
    assert tab.host.task is None and tab.host.task_ends == 1
    assert not tab.host.pending and tab.host_edit.isEnabled()
    assert tab.host.messages == [("Delete failed: RuntimeError: no worker available", True)]
