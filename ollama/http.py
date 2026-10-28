# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Abortable HTTP transport: the public API does not expose the core's transport."""
from __future__ import annotations
import http.client
import json
import socket
import sys
import urllib.parse
from contextlib import contextmanager
from typing import ClassVar
from slidemage.plugin_api import Cancelled, ProviderError

class HttpProvider:
    """The abortable JSON-over-HTTP plumbing both providers share."""

    service: ClassVar[str]  # "Ollama" / "Anthropic", for messages

    def __init__(self) -> None:
        self._sock: socket.socket | None = None     # of the running request, for abort()
        self._cancelled = False

    @staticmethod
    def _connection(parts: urllib.parse.SplitResult, timeout: float) -> http.client.HTTPConnection:
        """A not yet connected connection to the URL `parts` (http or https)."""
        connection_class = (http.client.HTTPSConnection if parts.scheme == "https"
                            else http.client.HTTPConnection)
        return connection_class(parts.hostname or "localhost", parts.port, timeout=timeout)

    @contextmanager
    def _request(self, method: str, base_url: str, endpoint: str, *, headers: dict[str, str],
                 payload: dict | None, timeout: float):
        """The response of one request, to read inside the ``with`` (a stream line by line);
        transport failures, while sending or reading, become ProviderError. abort() closes the
        socket under a blocked read."""
        parts = urllib.parse.urlsplit(base_url)
        conn = self._connection(parts, timeout)
        connected = False
        try:
            if self._cancelled:
                raise Cancelled("Aborted.")
            conn.connect()
            connected = True
            self._sock = conn.sock      # published before anything is sent, so that abort() reaches an upload too;
            if self._cancelled:         # kept here: a response that ends with the connection takes it from conn
                raise Cancelled("Aborted.")
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            conn.request(method, parts.path.rstrip("/") + endpoint, body=body, headers=headers)
            yield conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            if self._cancelled:
                raise Cancelled("Aborted.") from exc
            if not connected and isinstance(exc, (ConnectionError, socket.gaierror)):
                raise ProviderError(f"{self.service} unreachable: {exc}") from exc
            if isinstance(exc, (ConnectionError, http.client.RemoteDisconnected)):
                raise ProviderError(f"{self.service} dropped the connection: {exc}") from exc
            raise ProviderError(f"{self.service} request failed: {exc}") from exc
        finally:
            self._sock = None
            conn.close()

    def _send(self, method: str, base_url: str, endpoint: str, *, headers: dict[str, str],
              payload: dict | None, timeout: float) -> tuple[int, bytes]:
        """(status, body) of one request."""
        with self._request(method, base_url, endpoint, headers=headers, payload=payload,
                           timeout=timeout) as response:
            return response.status, response.read()

    def _json(self, status: int, raw: bytes, describe_error) -> dict:
        if status != 200:
            raise ProviderError(describe_error(status, error_detail(raw)))
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise ProviderError(f"{self.service} sent an unreadable response: {exc}") from exc
        return body if isinstance(body, dict) else {}

    def abort(self) -> None:
        self._cancelled = True
        _shutdown_socket(self._sock)


def _shutdown_socket(sock: socket.socket | None) -> None:
    """Wake a read blocked on `sock` in another thread."""
    if sock is None:
        return
    try:
        if sys.platform == "win32":
            # Measured 2026-09-26: on Windows neither socket.shutdown()
            # nor socket.close() wakes a recv() blocked in another
            # thread (the read just carries on until the server
            # answers); closing the handle itself with Winsock's
            # closesocket() does, at once. detach() first so Python's
            # own socket object no longer owns the handle and never
            # closes it a second time (by then possibly reused).
            import ctypes
            ctypes.windll.ws2_32.closesocket(ctypes.c_size_t(sock.detach()))
        else:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def error_detail(raw: bytes) -> str:
    """The server's own error text from a non-200 body (Ollama sends
    {"error": "..."}, Anthropic {"error": {"message": "..."}})."""
    text = raw.decode("utf-8", errors="replace")
    try:
        error = json.loads(text).get("error")
    except (ValueError, AttributeError):
        return text[:200]
    if isinstance(error, dict):
        error = error.get("message")
    return str(error or text[:200])


