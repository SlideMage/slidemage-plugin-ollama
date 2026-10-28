# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The fixtures the plugin's tests share."""
import pytest


@pytest.fixture
def server():
    """A local Ollama stand-in (fake_http.py): the test sets its status, body or script, then reads what it got."""
    from fake_http import Server

    srv = Server()
    yield srv
    srv.close()
