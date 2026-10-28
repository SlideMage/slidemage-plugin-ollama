# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport regressions ported from SlideMage PR #20."""
import ctypes
import errno
import socket
import ssl
import threading
import time
import urllib.parse
from types import SimpleNamespace

import pytest

from fake_http import ConnectProxy, StallingServer
from ollama.http import _shutdown_socket
from ollama.provider import OllamaProvider
from slidemage.plugin_api import Cancelled, ProviderError

def test_abort_interrupts_tcp_connect_polling(monkeypatch):
    p = OllamaProvider("m")
    p.host = "http://192.0.2.1"
    entered, closed = threading.Event(), threading.Event()
    outcome = []

    class Socket:
        def setblocking(self, _blocking):
            pass

        def connect_ex(self, _address):
            assert p._sock is self  # abort can reach even the first connect attempt
            entered.set()
            return errno.EINPROGRESS

        def shutdown(self, _how):
            closed.set()

        def close(self):
            closed.set()

    def select_pending(_read, _write, _exception, timeout):
        assert timeout <= 0.05
        closed.wait(timeout)
        return [], [], []

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 80))])
    monkeypatch.setattr(socket, "socket", lambda *_args: Socket())
    monkeypatch.setattr("ollama.http.select.select", select_pending)
    # These are Python socket doubles; the real Winsock path is checked separately.
    monkeypatch.setattr("ollama.http._shutdown_socket", lambda sock: sock.close() if sock else None)

    def run():
        try:
            p.chat([{"role": "user", "content": "hi"}], timeout=60)
        except ProviderError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        started = time.monotonic()
        p.abort()
        worker.join(1)
        assert not worker.is_alive() and time.monotonic() - started < 1
        assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
        assert closed.is_set() and p._sock is None
    finally:
        p.abort()
        closed.set()
        worker.join(2)


def test_connect_falls_back_from_unavailable_ipv6_and_restores_the_timeout(monkeypatch):
    p = OllamaProvider()
    calls = []

    class Socket:
        def setblocking(self, value):
            calls.append(("blocking", value))

        def connect_ex(self, target):
            assert p._sock is self
            calls.append(("connect", target))
            return errno.EINPROGRESS

        def getsockopt(self, *_args):
            return 0

        def settimeout(self, value):
            calls.append(("timeout", value))

        def close(self):
            calls.append("close")

    sock = Socket()

    def create(family, *_args):
        if family == socket.AF_INET6:
            raise OSError(errno.EAFNOSUPPORT, "IPv6 disabled")
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("::1", 80, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 80))])
    monkeypatch.setattr(socket, "socket", create)
    monkeypatch.setattr("ollama.http.select.select", lambda _r, w, _e, _t: ([], w, []))
    assert p._create_socket(("localhost", 80), 60) is sock
    assert calls == [("blocking", False), ("connect", ("127.0.0.1", 80)), ("timeout", 60)]


def test_tcp_connect_timeout_closes_the_published_socket(monkeypatch):
    p = OllamaProvider()
    closed = []

    class Socket:
        def setblocking(self, _value):
            pass

        def connect_ex(self, _target):
            return errno.EINPROGRESS

        def close(self):
            closed.append(True)

    clock = iter([0, 2])
    monkeypatch.setattr("ollama.http.time.monotonic", lambda: next(clock))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.0.2.1", 80))])
    monkeypatch.setattr(socket, "socket", lambda *_args: Socket())
    with pytest.raises(TimeoutError):
        p._create_socket(("192.0.2.1", 80), 1)
    assert closed == [True] and p._sock is None


def test_windows_abort_closes_the_detached_socket_handle(monkeypatch):
    calls = []

    class Socket:
        def detach(self):
            calls.append("detach")
            return 12345

        def close(self):
            calls.append("close")

    def closesocket(handle):
        calls.append(("closesocket", handle.value))

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(ws2_32=SimpleNamespace(closesocket=closesocket)), raising=False)
    monkeypatch.setattr("ollama.http.sys.platform", "win32")
    _shutdown_socket(Socket())
    assert calls == ["detach", ("closesocket", 12345), "close"]


@pytest.fixture
def proxy_environment(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy",
                 "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "")


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "127.0.0.1", "127.0.0.2", "::1"])
def test_local_ollama_is_never_proxied(proxy_environment, monkeypatch, scheme, host):
    monkeypatch.setenv(scheme.upper() + "_PROXY", "http://proxy.test:8080")
    authority = f"[{host}]" if ":" in host else host
    conn = OllamaProvider()._connection(urllib.parse.urlsplit(f"{scheme}://{authority}:11434"), 1)
    assert conn.host == host.lower() and conn._tunnel_host is None
    assert not getattr(conn, "_ollama_proxy", False)
    conn.close()


@pytest.mark.parametrize("scheme,variable", [("http", "HTTP_PROXY"), ("http", "http_proxy"),
                                            ("https", "HTTPS_PROXY"), ("https", "https_proxy"),
                                            ("https", "ALL_PROXY"), ("http", "all_proxy")])
def test_remote_proxy_configuration(proxy_environment, monkeypatch, scheme, variable):
    monkeypatch.setenv(variable, "http://user%40name:p%3Ass@proxy.test:8080")
    conn = OllamaProvider()._connection(urllib.parse.urlsplit(f"{scheme}://ollama.test:8443"), 10)
    assert (conn.host, conn.port) == ("proxy.test", 8080)
    if scheme == "https":
        assert (conn._tunnel_host, conn._tunnel_port) == ("ollama.test", 8443)
        assert conn._tunnel_headers["Proxy-Authorization"] == "Basic dXNlckBuYW1lOnA6c3M="
    else:
        assert conn._ollama_proxy_authorization == "Basic dXNlckBuYW1lOnA6c3M="
    conn.close()


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("variable", ["NO_PROXY", "no_proxy"])
def test_proxy_bypass_configuration(proxy_environment, monkeypatch, scheme, variable):
    monkeypatch.setenv(scheme.upper() + "_PROXY", "http://proxy.test:8080")
    monkeypatch.setenv(variable, ".ollama.test")
    conn = OllamaProvider()._connection(urllib.parse.urlsplit(f"{scheme}://remote.ollama.test"), 10)
    assert conn.host == "remote.ollama.test" and conn._tunnel_host is None
    assert not getattr(conn, "_ollama_proxy", False)
    conn.close()


def test_http_proxy_uses_absolute_request_target_and_proxy_auth(proxy_environment, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://user:pass@proxy.test:8080")
    seen = []
    provider = OllamaProvider("m", host="http://remote.test:11434/prefix")

    def connect(conn):
        def request(method, path, body, headers):
            seen.append((method, path, headers))
        conn.request = request
        conn.getresponse = lambda: SimpleNamespace(status=200, read=lambda: b'{}')

    monkeypatch.setattr(provider, "_connect", connect)
    provider.chat([{"role": "user", "content": "hi"}])
    assert seen[0][1] == "http://remote.test:11434/prefix/api/chat"
    assert seen[0][2]["Proxy-Authorization"] == "Basic dXNlcjpwYXNz"


def test_rejects_unsupported_proxy_scheme(proxy_environment, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "https://proxy.test")
    with pytest.raises(ProviderError, match="http:// proxy"):
        OllamaProvider()._connection(urllib.parse.urlsplit("https://remote.test"), 1)


@pytest.mark.parametrize("stage", ["tls", "proxy_tls", "connect_tunnel"])
def test_abort_ends_stalled_connection_setup(monkeypatch, stage):
    try:
        stalled = StallingServer()
    except PermissionError:
        pytest.skip("sandbox forbids loopback sockets")
    proxy = None
    p = OllamaProvider("m")
    outcome = []
    # Isolate both uppercase and lowercase environment settings.
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "*")
    p.host = f"https://127.0.0.1:{stalled.address[1]}"
    worker = None
    try:
        if stage != "tls":
            monkeypatch.setenv("NO_PROXY", "")
            if stage == "proxy_tls":
                proxy = ConnectProxy(stalled.address)
                monkeypatch.setenv("HTTPS_PROXY", proxy.url)
            else:
                monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{stalled.address[1]}")
            p.host = "https://ollama.test"

        def run():
            try:
                p.chat([{"role": "user", "content": "hi"}], timeout=60)
            except ProviderError as exc:
                outcome.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        assert stalled.received.wait(3), "no ClientHello / CONNECT received"
        assert p._sock is not None
        started = time.monotonic()
        p.abort()
        worker.join(1)
        assert not worker.is_alive() and time.monotonic() - started < 1
        assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
    finally:
        p.abort()
        stalled.close()
        if proxy:
            proxy.close()
        if worker:
            worker.join(2)


def test_abort_ends_a_pending_tcp_connect(monkeypatch):
    # TEST-NET-1: never a real API. Some networks reject immediately rather than
    # leaving the SYN pending; only a pending connection can exercise this case.
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    p = OllamaProvider("m")
    p.host = "https://192.0.2.1:443"
    outcome = []
    try:
        with socket.socket():
            pass
    except PermissionError:
        pytest.skip("sandbox forbids sockets")

    def run():
        try:
            p.chat([{"role": "user", "content": "hi"}], timeout=60)
        except ProviderError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 0.5
        while worker.is_alive() and time.monotonic() < deadline:
            worker.join(0.01)
        if not worker.is_alive() or p._sock is None:
            pytest.skip("network rejects the test address instead of leaving connect pending")
        # A completed TCP connection would have moved on to TLS; this test must
        # observe TCP setup itself, not another handshake timeout.
        if isinstance(p._sock, ssl.SSLSocket):
            pytest.skip("test address unexpectedly accepted TCP")
        started = time.monotonic()
        p.abort()
        worker.join(1)
        assert not worker.is_alive() and time.monotonic() - started < 1
        assert len(outcome) == 1 and isinstance(outcome[0], Cancelled)
    finally:
        p.abort()
        worker.join(2)


def test_abort_reaches_the_tls_socket_before_the_handshake(monkeypatch):
    p = OllamaProvider("m", host="https://ollama.test")
    entered, closed = threading.Event(), threading.Event()
    outcome = []

    class RawSocket:
        def setsockopt(self, *_args):
            pass

        def close(self):
            pass

    class TLSSocket:
        def do_handshake(self):
            assert p._sock is self
            entered.set()
            assert closed.wait(2), "abort did not close the TLS socket"
            raise OSError("handshake aborted")

        def shutdown(self, _how):
            closed.set()

        def close(self):
            closed.set()

    raw, tls = RawSocket(), TLSSocket()

    def create_socket(*_args):
        p._publish_socket(raw)
        return raw

    def wrap(_context, sock, *, server_hostname, do_handshake_on_connect):
        assert sock is raw and p._sock is raw
        assert server_hostname == "ollama.test" and not do_handshake_on_connect
        return tls

    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(p, "_create_socket", create_socket)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    monkeypatch.setattr("ollama.http._shutdown_socket", lambda sock: sock.close() if sock else None)

    def run():
        try:
            p.chat([{"role": "user", "content": "hi"}], timeout=60)
        except ProviderError as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert entered.wait(2)
        p.abort()
        worker.join(1)
        assert not worker.is_alive() and len(outcome) == 1 and isinstance(outcome[0], Cancelled)
        assert p._sock is None
    finally:
        p.abort()
        closed.set()
        worker.join(2)



@pytest.mark.parametrize("scheme", ["http", "https"])
def test_remote_request_through_proxy(proxy_environment, monkeypatch, scheme):
    from fake_http import TEST_TLS_CERT, Server

    try:
        server = Server(tls=scheme == "https")
    except PermissionError:
        pytest.skip("sandbox forbids loopback sockets")
    proxy = None
    try:
        server.body = {"message": {"content": "via proxy"}}
        if scheme == "https":
            context = ssl.create_default_context(cafile=str(TEST_TLS_CERT))
            monkeypatch.setattr(ssl, "_create_default_https_context", lambda: context)
            proxy = ConnectProxy(server.httpd.server_address)
            proxy_url = proxy.url
            # The certificate covers api.anthropic.test from the upstream fixture.
            host = "api.anthropic.test"
        else:
            proxy_url = server.url
            host = "ollama.test"
        monkeypatch.setenv(scheme.upper() + "_PROXY", proxy_url.replace("http://", "http://user:pass@"))
        p = OllamaProvider("m", host=f"{scheme}://{host}:8443/prefix")
        assert p.chat([{"role": "user", "content": "hi"}]).text == "via proxy"
        if proxy:
            assert proxy.requests[0]["path"] == f"{host}:8443"
            assert proxy.requests[0]["headers"]["proxy-authorization"] == "Basic dXNlcjpwYXNz"
            assert "proxy-authorization" not in server.requests[0]["headers"]
            assert server.requests[0]["path"] == "/prefix/api/chat"
        else:
            assert server.requests[0]["path"] == f"http://{host}:8443/prefix/api/chat"
            assert server.requests[0]["headers"]["proxy-authorization"] == "Basic dXNlcjpwYXNz"
        assert server.requests[0]["headers"]["host"] == f"{host}:8443"
    finally:
        if proxy:
            proxy.close()
        server.close()
