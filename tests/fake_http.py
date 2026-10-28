# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A throwaway local HTTP server standing in for Ollama (no external network), and the streaming
wire format of its /api/chat for it to answer with."""
from __future__ import annotations

import http.client
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Pipe:
    """A blocking byte stream for the sandbox's in-memory HTTP transport."""

    def __init__(self):
        self.data = bytearray()
        self.ended = False
        self.ready = threading.Condition()

    def readinto(self, buffer):
        with self.ready:
            if not self.ready.wait_for(lambda: self.data or self.ended, timeout=5):
                raise TimeoutError("test HTTP stream stalled")
            size = min(len(buffer), len(self.data))
            buffer[:size] = self.data[:size]
            del self.data[:size]
            return size

    def send(self, data):
        with self.ready:
            if self.ended:
                raise BrokenPipeError("test HTTP stream closed")
            self.data.extend(data)
            self.ready.notify_all()

    def end(self):
        with self.ready:
            self.ended = True
            self.ready.notify_all()


class _Reader(io.RawIOBase):
    def __init__(self, pipe):
        self.pipe = pipe

    def readable(self):
        return True

    def readinto(self, buffer):
        return self.pipe.readinto(buffer)


class _MemorySocket:
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing

    def makefile(self, mode, buffering=-1):
        assert mode == "rb"
        return io.BufferedReader(_Reader(self.incoming))

    def sendall(self, data):
        self.outgoing.send(data)

    def shutdown(self, how):
        self.incoming.end()
        self.outgoing.end()

    def close(self):
        self.outgoing.end()


class Server:
    """Answers every request with `status`/`body` (or, for a path in `routes`, with that route's
    (status, body); a `script` is used up first, one entry per request: a body, or a (status, body) or
    (status, body, headers) tuple) and records what it got."""

    def __init__(self, *, in_memory: bool = False):
        self.status = 200
        self.body: dict | str = {}
        self.content_type = "application/json"
        self.delay = threading.Event()  # set to release a held response
        self.hold = False
        self.stall_after: int | None = None   # answer only this many bytes of the body, then wait
        self.script: list = []   # answers for the next requests, in turn (else `body`)
        self.routes: dict[str, tuple[int, dict | str]] = {}
        self.requests: list[dict] = []
        self._threads: list[threading.Thread] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length)
                outer.requests.append({
                    "method": self.command, "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": json.loads(raw) if raw else None})
                if outer.hold:
                    outer.delay.wait(5)
                status, body = outer.routes.get(self.path, (outer.status, outer.body))
                extra: dict[str, str] = {}
                if outer.script:
                    entry = outer.script.pop(0)
                    if isinstance(entry, tuple):
                        status, body, *rest = entry
                        extra = rest[0] if rest else {}
                    else:
                        body = entry
                data = (body if isinstance(body, str) else json.dumps(body)).encode()
                try:
                    if outer.stall_after is not None:      # a stream that goes quiet in the middle
                        self.send_response(status)
                        self.send_header("Content-Type", outer.content_type)
                        self.end_headers()
                        self.wfile.write(data[:outer.stall_after])
                        self.wfile.flush()
                        outer.delay.wait(5)
                        return
                    self.send_response(status)
                    self.send_header("Content-Type", outer.content_type)
                    self.send_header("Content-Length", str(len(data)))
                    for name, value in extra.items():
                        self.send_header(name, value)
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client aborted

            do_GET = do_POST = _answer

            def log_message(self, *_args):
                pass

        self._handler = Handler
        self.httpd = None
        if in_memory:
            self.url = "http://ollama.test"
            return
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()

    def connection(self, parts, timeout):
        """Stdlib HTTP over blocking byte streams when sockets are sandboxed.

        Only this server's synthetic address connects; all others are refused.
        HTTP encoding and streaming still use the stdlib client/server. Shutdown
        wakes the reader for abort tests; this mode does not test OS socket behaviour.
        """
        connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)

        def connect():
            if parts.hostname != "ollama.test" or parts.port is not None:
                raise ConnectionRefusedError("no test server at this address")
            request, response = _Pipe(), _Pipe()
            client = _MemorySocket(response, request)
            server = _MemorySocket(request, response)
            connection.sock = client

            def serve():
                try:
                    self._handler(server, ("local", 0), self)
                finally:
                    server.close()

            thread = threading.Thread(target=serve, daemon=True)
            self._threads.append(thread)
            thread.start()

        connection.connect = connect
        return connection

    def close(self):
        self.delay.set()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        for thread in self._threads:
            thread.join(5)


def ollama_ndjson(pieces=(), tool_calls=(), done_reason: str = "stop") -> str:
    """The lines /api/chat streams: one per text piece, then one carrying `tool_calls` (a list of
    (name, arguments) pairs, each whole), then the closing statistics."""
    lines = [{"model": "m", "message": {"role": "assistant", "content": piece}, "done": False}
             for piece in pieces]
    if tool_calls:
        lines.append({"model": "m", "done": False, "message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": name, "arguments": arguments}} for name, arguments in tool_calls]}})
    lines.append({"model": "m", "message": {"role": "assistant", "content": ""}, "done": True,
                  "done_reason": done_reason, "prompt_eval_count": 12, "eval_count": 5})
    return "".join(json.dumps(line) + "\n" for line in lines)


def wait_for_first_piece(thread, texts, seconds=5.0):
    """Until the streaming thread has handed over its first text; fails instead of spinning when it ended without."""
    end = time.monotonic() + seconds
    while not texts:
        assert thread.is_alive(), "the stream ended before its first piece"
        assert time.monotonic() < end, "no first piece in time"
        thread.join(0.01)
