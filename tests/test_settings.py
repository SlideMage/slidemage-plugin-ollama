# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real Qt widgets with a queued host: worker boundaries, task slots and page lifetime."""
from __future__ import annotations

import gc
import weakref

import pytest

from ollama.admin import Progress
from fake_host import FakeHost, FakeSettings
from ollama.tasks import TRANSLATION
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
    page = route.build_settings_section()
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
    page = route.build_settings_section()
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
    tab.host.route_tasks["ollama"] = "another core AI job"
    getattr(tab, f"_on_{operation}")()
    assert not tab.host.pending
    assert tab.host.route_tasks["ollama"] == "another core AI job"


@pytest.mark.parametrize("operation", ["delete", "update"])
def test_cancelled_confirmation_leaves_no_slot(tab, monkeypatch, operation):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.No)
    getattr(tab, f"_on_{operation}")()
    assert not tab.host.route_tasks and not tab.host.pending


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
            kwargs["on_progress"](Progress("pulling weights"))
        if outcome == "exception":
            raise ValueError("broken reply")
        return outcome == "success", "" if outcome == "success" else "server refused"

    monkeypatch.setattr(admin, f"{operation}_model", work)
    getattr(tab, f"_on_{operation}")()
    assert "ollama" in tab.host.route_tasks and len(tab.host.pending) == 1
    assert not tab.host_edit.isEnabled() and not tab.update_button.isEnabled()
    tab.host.store.set("ollama_host", "http://changed-while-running")
    tab.page.deleteLater()
    _destroy_pages()
    assert not tab._alive
    tab.host.finish_next()
    assert calls == [("qwen3", "http://original:11434")]
    if operation == "update":
        assert ("pulling weights", False) in tab.host.messages  # progress goes to the main window now
    assert not tab.host.route_tasks
    errors = [message for message in tab.host.messages if message[1]]
    assert bool(errors) == (outcome != "success")
    tab.host.finish_next()  # refresh the route's cache, without the page
    assert tab.route.model_choices(tab.host.context(), TRANSLATION) == ["qwen3"]


def test_scheduling_failure_releases_slot_and_reports_error(tab, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    tab.host.background_error = RuntimeError("no worker available")
    tab._on_delete()
    assert not tab.host.route_tasks
    assert not tab.host.pending and tab.host_edit.isEnabled()
    assert tab.host.messages == [("Delete failed: RuntimeError: no worker available", True)]


def test_confirmation_error_releases_shared_lease(tab, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    def fail(*_args):
        raise RuntimeError("dialog failed")

    monkeypatch.setattr(QMessageBox, "question", fail)
    with pytest.raises(RuntimeError, match="dialog failed"):
        tab._on_delete()
    assert not tab.host.route_tasks


def _start_update(tab, monkeypatch, reports):
    """Starts an update whose work makes `reports`; returns the worker's done callback, with the work run."""
    from PySide6.QtWidgets import QMessageBox
    from ollama import admin

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)

    def work(model, address, on_progress):
        for report in reports:
            on_progress(report)
        return True, ""

    monkeypatch.setattr(admin, "update_model", work)
    tab._on_update()
    run, done = tab.host.pending.pop(0)
    results = run()
    return lambda: done(results, None)


def test_update_shows_determinate_bar_and_hides_it_afterwards(tab, monkeypatch):
    assert tab.progress_bar.isHidden()
    done = _start_update(tab, monkeypatch, [Progress("qwen3: pulling abc (42%)", 0.42)])
    tab.host.deliver_progress()
    assert not tab.progress_bar.isHidden()
    assert (tab.progress_bar.minimum(), tab.progress_bar.maximum(), tab.progress_bar.value()) == (0, 100, 42)
    assert tab.status.text() == "qwen3: pulling abc (42%)"
    done()
    assert tab.progress_bar.isHidden()
    assert any("Updating Ollama model" in line for line in tab.host.logs)
    assert not any("42%" in line for line in tab.host.logs)  # no log line per tick
    tab.host.finish_next()


def test_update_without_fraction_is_indeterminate(tab, monkeypatch):
    done = _start_update(tab, monkeypatch, [Progress("qwen3: pulling manifest")])
    tab.host.deliver_progress()
    assert not tab.progress_bar.isHidden()
    assert (tab.progress_bar.minimum(), tab.progress_bar.maximum()) == (0, 0)
    done()
    assert tab.progress_bar.isHidden()
    tab.host.finish_next()


def test_several_models_say_which_one_is_updating(tab, monkeypatch):
    from ollama.settings import _update_models
    from ollama import admin

    monkeypatch.setattr(admin, "update_model", lambda *_a, on_progress: on_progress(Progress("x: verifying")) or (True, ""))
    seen = []
    _update_models("http://h", ["a", "b"], seen.append)
    assert [p.text for p in seen] == [
        "Updating Ollama model a… (model 1 of 2)", "x: verifying (model 1 of 2)",
        "Updating Ollama model b… (model 2 of 2)", "x: verifying (model 2 of 2)"]


def test_delete_shows_indeterminate_bar(tab, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from ollama import admin

    monkeypatch.setattr(QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(admin, "delete_model", lambda *_a: (True, ""))
    tab._on_delete()
    assert not tab.progress_bar.isHidden() and tab.progress_bar.maximum() == 0
    assert tab.status.text() == "Deleting model…"
    tab.host.finish_next()
    assert tab.progress_bar.isHidden()
    tab.host.finish_next()
