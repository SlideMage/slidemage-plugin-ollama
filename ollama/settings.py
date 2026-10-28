# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Ollama section of Settings → AI, built with the draft host's widgets.

Discovery and administration use run_in_background, whose completion callback is
independent of the page's lifetime. Only the UI callback releases the app-wide
Ollama slot; workers use a captured address and never access settings or widgets.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QListWidget, QListWidgetItem, QMessageBox

from . import admin
from .host import Host
from .provider import InstalledModel, ollama_host

if TYPE_CHECKING:
    from .route import OllamaRoute

Results = list[tuple[str, bool, str]]      # (model, ok, error)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _delete_models(host: Host, address: str, models: list[str]) -> Results:
    results: Results = []
    for model in models:
        ok, error = admin.delete_model(model, address)
        results.append((model, ok, error or "Unknown error"))
        if ok:
            host.log(f"Deleted Ollama model {model}")
    return results


def _update_models(host: Host, address: str, models: list[str]) -> Results:
    results: Results = []
    for model in models:
        host.log(f"Updating Ollama model {model}…")
        ok, error = admin.update_model(model, address, on_progress=host.log)
        results.append((model, ok, error))
        host.log(f"Updated Ollama model {model}" if ok else f"Failed to update {model}: {error}")
    return results


class OllamaTab(QObject):
    """Controller of a host Page; composition avoids inheriting a private Page class."""

    def __init__(self, host: Host, route: OllamaRoute) -> None:
        page = host.Page()
        super().__init__(page)  # the page owns the controller after the builder returns
        self.host, self.route = host, route
        self.page = page
        self._alive = True
        self._busy = False
        self.page.destroyed.connect(self._on_destroyed)

        self.host_edit = QLineEdit(ollama_host(host))
        self.host_edit.setPlaceholderText("http://localhost:11434")
        self.host_edit.editingFinished.connect(self._on_host)
        refresh = host.Button("Refresh", compact=True)
        refresh.clicked.connect(self.refresh)
        self.page.body.addWidget(host.Field("Ollama address", host.row(self.host_edit, refresh)))
        self.status = host.caption("")
        self.page.body.addWidget(self.status)

        self.models_list = QListWidget()
        self.models_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.models_list.setMinimumHeight(190)
        self.models_list.itemSelectionChanged.connect(self._sync_buttons)
        self.page.body.addWidget(host.Field("Installed models", self.models_list))
        self.update_button = host.Button("Update selected…", compact=True)
        self.update_button.clicked.connect(self._on_update)
        self.delete_button = host.Button("Delete selected…", compact=True)
        self.delete_button.clicked.connect(self._on_delete)
        self.page.body.addWidget(host.row(host.caption(
            "Update pulls the latest weights (a custom model you built with ollama create is relinked to "
            "its refreshed base). Delete removes the files from disk and cannot be undone short of "
            "pulling the model again."), self.update_button, self.delete_button))
        self.page.body.addStretch(1)
        self.refresh()

    def _on_destroyed(self, *_args) -> None:
        self._alive = False

    def refresh(self) -> None:
        if not self._alive or self._busy:
            return
        self.status.setText("Asking Ollama…")
        self.models_list.setEnabled(False)
        self._sync_buttons()

        def loaded(installed: list[InstalledModel]) -> None:
            if self._alive and not self._busy:
                self.fill(installed)
                self.page.changed.emit()

        self.route.refresh_models(loaded)

    def fill(self, installed: list[InstalledModel]) -> None:
        self.models_list.clear()
        self.models_list.setEnabled(bool(installed))
        self.status.setText(
            f"{len(installed)} model(s) installed." if installed else
            f"Ollama is not reachable at {ollama_host(self.host)}, or has no models installed.")
        for model in installed:
            item = QListWidgetItem(f"{model.name}   —   {_format_size(model.size_bytes)}"
                                   + ("   (vision)" if "vision" in model.capabilities else ""))
            item.setData(Qt.ItemDataRole.UserRole, model.name)
            self.models_list.addItem(item)
        self._sync_buttons()

    def selected(self) -> list[str]:
        return [item.data(Qt.ItemDataRole.UserRole) for item in self.models_list.selectedItems()]

    def _sync_buttons(self) -> None:
        enabled = bool(self.selected()) and self.models_list.isEnabled() and not self._busy
        self.update_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _on_host(self) -> None:
        settings = self.host.settings()
        previous = settings.get("ollama_host")
        settings.set("ollama_host", self.host_edit.text().strip())
        try:
            settings.save()
        except OSError as exc:
            settings.set("ollama_host", previous)
            self.host_edit.setText(ollama_host(self.host))
            self.host.show_message(f"Could not save Ollama address: {exc}", error=True)
            return
        self.host_edit.setText(ollama_host(self.host))
        self.host.log(f"Ollama host set to {ollama_host(self.host)}")
        self.page.changed.emit()
        self.refresh()

    def _on_delete(self) -> None:
        models = self.selected()
        if not models:
            return
        what = f"deleting {len(models)} model(s)" if len(models) > 1 else f"deleting {models[0]}"
        if not self.host.try_start_ollama_task(what):
            return
        detail = (f'"{models[0]}"' if len(models) == 1 else
                  f"these {len(models)} models:\n\n" + "\n".join(f"  • {m}" for m in models))
        try:
            confirm = QMessageBox.question(
                self.page, "Delete Ollama model" + ("s" if len(models) > 1 else ""),
                f"Permanently delete {detail} from Ollama's model store? This removes the file(s) from "
                "disk and cannot be undone short of pulling the model again.\n\n"
                "Safe even if another installed model was created from one of these (ollama create) and "
                "shares its files: like \"ollama rm\", Ollama only removes files no other installed model "
                "still references.")
        except BaseException:
            self.host.end_ollama_task()
            raise
        if confirm != QMessageBox.StandardButton.Yes:
            self.host.end_ollama_task()
            return
        host, address = self.host, ollama_host(self.host)
        self._run(lambda: _delete_models(host, address, models), "Deleted", "Delete failed")

    def _on_update(self) -> None:
        models = self.selected()
        if not models:
            return
        detail = (f'"{models[0]}"' if len(models) == 1 else
                  f"these {len(models)} models:\n\n" + "\n".join(f"  • {m}" for m in models))
        confirm = QMessageBox.question(
            self.page, "Update Ollama model" + ("s" if len(models) > 1 else ""),
            f"Update {detail} to the latest version?\n\n"
            "This re-downloads the underlying weights (can be several GB per model and take a while) and "
            "cannot be cancelled once started.")
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if not self.host.try_start_ollama_task(
                f"updating {len(models)} model(s)" if len(models) > 1 else f"updating {models[0]}"):
            return
        host, address = self.host, ollama_host(self.host)
        self._run(lambda: _update_models(host, address, models), "Updated", "Update failed")

    def _run(self, work: Callable[[], Results], done: str, failed_title: str) -> None:
        self._busy = True
        self.host_edit.setEnabled(False)
        self.models_list.setEnabled(False)
        self._sync_buttons()
        host = self.host

        def finished(results: Results | None, error: BaseException | None) -> None:
            host.end_ollama_task()  # UI thread, even after the page was destroyed
            self._busy = False
            if error is not None:
                host.log(f"{failed_title}: {error}")
                host.show_message(f"{failed_title}: {type(error).__name__}: {error}", error=True)
            else:
                failures = [(model, error) for model, ok, error in results or [] if not ok]
                if failures:
                    host.show_message(
                        f"{done} {len(results or []) - len(failures)} of {len(results or [])}. Failed:\n"
                        + "\n".join(f"{model}: {error}" for model, error in failures), error=True)
            if self._alive:
                self.host_edit.setEnabled(True)
                self.refresh()
            else:
                self.route.refresh_models()

        try:
            host.run_in_background(work, finished)
        except Exception as exc:  # the host could not schedule the work
            finished(None, exc)
