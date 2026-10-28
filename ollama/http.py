# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Abortable HTTP transport: the public API does not expose the core's transport."""
from __future__ import annotations
import base64
import errno
import http.client
import ipaddress
import json
import os
import select
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import ClassVar
from slidemage.plugin_api import Cancelled, ProviderError

class HttpProvider:
    """The abortable JSON-over-HTTP plumbing both providers share."""

    service: ClassVar[str]  # "Ollama" / "Anthropic", for messages

    def __init__(self) -> None:
        self._sock: socket.socket | None = None     # of the running request, for abort()
        self._cancelled = False
        self._sock_lock = threading.Lock()

    # Local copy of SlideMage's AI transport (PR #20); plugin_api does not export it.
    # HTTP proxy forwarding is added here for remote HTTP Ollama servers.
    def _connection(self, parts: urllib.parse.SplitResult, timeout: float) -> http.client.HTTPConnection:
        host = parts.hostname or "localhost"
        connection_class = (http.client.HTTPSConnection if parts.scheme == "https"
                            else http.client.HTTPConnection)
        local = host.rstrip(".").lower() == "localhost"
        try:
            local = local or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        proxy_url = None
        if not local and not urllib.request.proxy_bypass(parts.netloc):
            proxies = urllib.request.getproxies()
            proxy_url = proxies.get(parts.scheme) or proxies.get("all")
        if not proxy_url:
            return connection_class(host, parts.port, timeout=timeout)
        proxy = urllib.parse.urlsplit(proxy_url if "://" in proxy_url else "http://" + proxy_url)
        if proxy.scheme != "http" or not proxy.hostname:
            raise ProviderError("HTTP and HTTPS requests require an http:// proxy.")
        authorization = None
        if proxy.username is not None:
            credentials = (urllib.parse.unquote(proxy.username) + ":" +
                           urllib.parse.unquote(proxy.password or ""))
            authorization = "Basic " + base64.b64encode(credentials.encode()).decode("ascii")
        conn = connection_class(proxy.hostname, proxy.port or 80, timeout=timeout)
        if parts.scheme == "https":
            headers = {"Proxy-Authorization": authorization} if authorization else {}
            conn.set_tunnel(host, parts.port or 443, headers=headers)
        else:
            conn._ollama_proxy = True
            conn._ollama_proxy_authorization = authorization
        return conn

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise Cancelled("Aborted.")

    def _publish_socket(self, sock: socket.socket) -> None:
        with self._sock_lock:
            self._check_cancelled()
            self._sock = sock

    def _create_socket(self, address: tuple[str, int], timeout: float,
                       source_address=None) -> socket.socket:
        """Connect with short polls; publish each candidate before starting TCP setup.

        Closing a socket does not interrupt connect() on every OS. Nonblocking
        connect and bounded select waits also cover that case, including Windows.
        """
        last_error = None
        for family, kind, proto, _, target in socket.getaddrinfo(*address, type=socket.SOCK_STREAM):
            self._check_cancelled()
            sock = None
            connected = False
            try:
                sock = socket.socket(family, kind, proto)
                self._publish_socket(sock)
                sock.setblocking(False)
                if source_address:
                    sock.bind(source_address)
                deadline = time.monotonic() + timeout
                error = sock.connect_ex(target)
                if error not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                    raise OSError(error, os.strerror(error))
                while error:
                    self._check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("timed out")
                    _, writable, exceptional = select.select([], [sock], [sock], min(0.05, remaining))
                    if writable or exceptional:
                        error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if error:
                            raise OSError(error, os.strerror(error))
                self._check_cancelled()
                sock.settimeout(timeout)
                connected = True
                return sock
            except OSError as exc:
                self._check_cancelled()
                last_error = exc
            except ValueError:
                # abort() can detach the Windows handle just before select().
                self._check_cancelled()
                raise
            finally:
                if sock is not None and not connected:
                    with self._sock_lock:
                        if self._sock is sock:
                            self._sock = None
                        sock.close()
        self._check_cancelled()
        raise last_error or OSError("getaddrinfo returned no addresses")

    def _connect(self, conn: http.client.HTTPConnection) -> None:
        """Open TCP, optional CONNECT, then TLS, keeping abort's socket current."""
        self._check_cancelled()
        conn._create_connection = self._create_socket
        if isinstance(conn, http.client.HTTPSConnection):
            # HTTPConnection.connect performs CONNECT on the published raw socket.
            http.client.HTTPConnection.connect(conn)
            with self._sock_lock:
                self._check_cancelled()
                # wrap_socket transfers the fd: publish the SSLSocket before the
                # blocking handshake, and keep abort from closing the old owner.
                conn.sock = conn._context.wrap_socket(
                    conn.sock, server_hostname=conn._tunnel_host or conn.host,
                    do_handshake_on_connect=False)
                self._sock = conn.sock
            conn.sock.do_handshake()
        else:
            conn.connect()
        self._publish_socket(conn.sock)

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
            self._connect(conn)
            connected = True
            body = None if payload is None else json.dumps(payload).encode("utf-8")
            path = parts.path.rstrip("/") + endpoint
            if getattr(conn, "_ollama_proxy", False):
                host = parts.hostname or "localhost"
                authority = f"[{host}]" if ":" in host else host
                if parts.port is not None:
                    authority += f":{parts.port}"
                path = urllib.parse.urlunsplit((parts.scheme, authority, path, "", ""))
                if conn._ollama_proxy_authorization:
                    headers = {**headers, "Proxy-Authorization": conn._ollama_proxy_authorization}
            conn.request(method, path, body=body, headers=headers)
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
            with self._sock_lock:
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
        with self._sock_lock:
            self._cancelled = True
            _shutdown_socket(self._sock)


def _shutdown_socket(sock: socket.socket | None) -> None:
    """Wake connection setup or a read blocked on `sock` in another thread."""
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
    finally:
        sock.close()


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


