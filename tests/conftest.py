# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The fixtures the plugin's tests share."""
import pytest


@pytest.fixture
def server(monkeypatch):
    """A local Ollama stand-in (fake_http.py): the test sets its status, body or script, then reads what it got."""
    from fake_http import Server

    try:
        srv = Server()
    except PermissionError:
        # Keep exercising HTTP serialization and abort signalling when the
        # runner forbids sockets. Normal CI still tests real loopback TCP.
        from ollama.http import HttpProvider

        srv = Server(in_memory=True)
        monkeypatch.setattr(HttpProvider, "_connection", staticmethod(srv.connection))
    yield srv
    srv.close()
