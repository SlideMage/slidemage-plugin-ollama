# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Ollama section of Settings → AI, built with public host widgets.

Discovery and administration run off-thread. The host's route lease coordinates
administration with AI work, and progress and completion return to the UI thread.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QWidget)

from . import admin
from slidemage.plugin_api import Host, RouteTaskLease
from .provider import InstalledModel, ollama_host

if TYPE_CHECKING:
    from .route import OllamaRoute

def _row(*widgets: QWidget) -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    return row


Results = list[tuple[str, bool, str]]      # (model, ok, error)


def _format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit in ("B", "KB") else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _delete_models(address: str, models: list[str]) -> Results:
    results: Results = []
    for model in models:
        ok, error = admin.delete_model(model, address)
        results.append((model, ok, error or "Unknown error"))
    return results


def _update_models(address: str, models: list[str], report: Callable[[str], None]) -> Results:
    results: Results = []
    for model in models:
        report(f"Updating Ollama model {model}…")
        ok, error = admin.update_model(model, address, on_progress=report)
        results.append((model, ok, error))
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
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        self.page.body.addWidget(host.Field("Ollama address", _row(self.host_edit, refresh)))
        self.status = host.caption("")
        self.page.body.addWidget(self.status)

        self.models_list = QListWidget()
        self.models_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.models_list.setMinimumHeight(190)
        self.models_list.itemSelectionChanged.connect(self._sync_buttons)
        self.page.body.addWidget(host.Field("Installed models", self.models_list))
        self.update_button = QPushButton("Update selected…")
        self.update_button.clicked.connect(self._on_update)
        self.delete_button = QPushButton("Delete selected…")
        self.delete_button.clicked.connect(self._on_delete)
        self.page.body.addWidget(_row(host.caption(
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
        self.host.model_choices_changed(self.route.id)

    def _on_delete(self) -> None:
        models = self.selected()
        if not models:
            return
        what = f"deleting {len(models)} model(s)" if len(models) > 1 else f"deleting {models[0]}"
        lease = self.host.acquire_route_task(self.route.id, what)
        if lease is None:
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
            lease.release()
            raise
        if confirm != QMessageBox.StandardButton.Yes:
            lease.release()
            return
        try:
            address = ollama_host(self.host)
            self._run(lambda _report: _delete_models(address, models), "Deleted", "Delete failed", lease)
        except BaseException:
            lease.release()
            raise

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
        lease = self.host.acquire_route_task(
            self.route.id, f"updating {len(models)} model(s)" if len(models) > 1 else f"updating {models[0]}")
        if lease is None:
            return
        try:
            address = ollama_host(self.host)
            self._run(lambda report: _update_models(address, models, report), "Updated", "Update failed", lease)
        except BaseException:
            lease.release()
            raise

    def _run(self, work: Callable[[Callable[[str], None]], Results], done: str, failed_title: str,
             lease: RouteTaskLease) -> None:
        self._busy = True
        self.host_edit.setEnabled(False)
        self.models_list.setEnabled(False)
        self._sync_buttons()
        host = self.host

        def finished(results: Results | None, error: BaseException | None) -> None:
            lease.release()  # UI thread, even after the page was destroyed
            self._busy = False
            if error is not None:
                host.log(f"{failed_title}: {error}")
                host.show_message(f"{failed_title}: {type(error).__name__}: {error}", error=True)
            else:
                for model, ok, detail in results or []:
                    host.log(f"{done} Ollama model {model}" if ok else f"{failed_title} for {model}: {detail}")
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
            host.run_in_background_with_progress(work, host.log, finished)
        except Exception as exc:  # the host could not schedule the work
            finished(None, exc)
