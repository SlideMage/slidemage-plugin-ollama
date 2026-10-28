# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The minimum host contract for the Ollama plugin, drafted from the plugin side.

DRAFT until slidemage.plugin_api has them: the AI contracts, neutral message types,
HttpProvider and error_detail below are local stand-ins, not a released host API.
The neutral implementations retain SlideMage's current wire/abort behaviour so the
plugin can be tested independently. Replace them with public API imports together;
exception identity and isinstance checks on agent messages must match the host.

Registrations happen only in activate(), without network or background work. Host
calls and callbacks run on the UI thread, except run_in_background's work and log
(which must also accept worker-thread progress). Widgets are PySide6 objects, kept
as Any so activation and this contract need neither Qt nor SlideMage at import.
"""
from __future__ import annotations

import http.client
import json
import socket
import sys
import urllib.parse
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, Protocol, TypeVar

Widget = Any
T = TypeVar("T")
Message = dict[str, str]

CHAT, IMAGE_DESCRIPTION, TRANSLATION, FIXES = "chat", "image_description", "translation", "fixes_notes_transcripts"
TASKS = (CHAT, IMAGE_DESCRIPTION, TRANSLATION, FIXES)
TASK_LABELS = {CHAT: "Chat", IMAGE_DESCRIPTION: "Image descriptions", TRANSLATION: "Translation",
               FIXES: "Fixes, notes, transcripts"}

# DRAFT until slidemage.plugin_api has them: provider-neutral data and HTTP support.

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    schema: dict       # JSON schema of the arguments (an "object")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ToolResult:
    call_id: str
    text: str
    is_error: bool = False
    images: list[bytes] = field(default_factory=list)   # PNG/JPEG bytes the model should look at


@dataclass
class UserMessage:
    text: str           # what the model is sent
    shown: str = ""     # what the user typed, if `text` carries more (the context block): what the chat shows


@dataclass
class AssistantMessage:
    """One finished model turn. `stop` is "end_turn", "tool_use", "max_tokens" or "refusal".
    `raw` is whatever the provider needs to replay this turn faithfully (Claude: the content
    blocks, thinking included); it is opaque to everyone else and never persisted."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end_turn"
    raw: Any = None
    usage: dict[str, int] = field(default_factory=dict)   # input/output/cache_read/cache_write tokens


@dataclass
class ToolResultsMessage:
    results: list[ToolResult]


AgentMessage = UserMessage | AssistantMessage | ToolResultsMessage


class ProviderError(Exception):
    """A request failed; str(exc) is the message to show the user."""


class Cancelled(ProviderError):
    """abort() ended the request -- the user asked for it, not a failure."""


@dataclass
class ChatReply:
    text: str
    thinking: str = ""      # Ollama thinking models only
    truncated: bool = False  # stopped at max_tokens rather than finishing


class ChatProvider(Protocol):
    model: str
    is_local: bool

    @property
    def label(self) -> str:
        """``qwen3`` / ``claude-sonnet-5-5 · cloud`` -- what to show the
        user as the answering model."""

    def chat(
        self, messages: list[Message], *, system: str = "",
        images: list[bytes] | None = None, schema: dict | None = None,
        temperature: float = 0.2, max_tokens: int = 2048, timeout: float = 300.0,
        think: bool = False, num_ctx: int | None = None,
    ) -> ChatReply:
        """One non-streaming turn. `images` attach to the last message;
        `schema` (a JSON schema) asks for a JSON object back."""

    def list_models(self) -> list[str]:
        """Model ids to offer, best first; [] (never an error) when the
        service cannot be reached."""

    def abort(self) -> None:
        """Ends a running chat() from another thread (it raises Cancelled).
        One-shot: make a new provider for the next abortable operation."""

    def stream_turn(
        self, system: str, messages: list[AgentMessage], tools: list[ToolSpec],
        on_text: Callable[[str], None], *, max_tokens: int = 16000, timeout: float = 300.0,
    ) -> AssistantMessage:
        """One streamed turn of the agent chat."""
        ...


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


# DRAFT until slidemage.plugin_api has them: phase 3a/3c/3d contracts.
# The plan leaves ctx and chat_surface's arguments unspecified. Ollama does not
# inspect ctx; its address and task choices belong to this machine's plugin store.
class ResolvedRoute(Protocol):
    task: str
    model: str


class AiRoute(Protocol):
    id: str
    service: str
    is_local: bool
    tasks: frozenset[str]

    def problem(self, ctx: Any, task: str) -> str: ...
    def model_choices(self, ctx: Any, task: str) -> list[str]: ...
    def default_choice(self, task: str) -> str: ...
    def choice_label(self, choice: str) -> str: ...
    def provider(self, ctx: Any, route: ResolvedRoute) -> ChatProvider | None: ...
    # None asks core to wrap provider(), run its provider tasks, or use its chat view.
    def draft_backend(self, ctx: Any, route: ResolvedRoute) -> Any | None: ...
    def translation_backend(self, ctx: Any, route: ResolvedRoute) -> Any | None: ...
    def slide_translator(self, ctx: Any, route: ResolvedRoute) -> Any | None: ...
    def task_runner(self, ctx: Any, route: ResolvedRoute) -> Any | None: ...
    def chat_surface(self, *args: Any, **kwargs: Any) -> Widget | None: ...
    def settings_section(self) -> Widget: ...


class Settings(Protocol):
    """The plugin's own persisted JSON store; matches the existing host API."""

    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def save(self) -> None:
        """Persist pending values; raise OSError on failure."""
        ...


class Host(Protocol):
    """The calls this plugin needs, not a claim that the current host implements them."""

    def add_ai_route(self, route: AiRoute) -> None:
        """Register during activation; this must not query the route's server."""
        ...

    def add_settings_section(self, page: Literal["ai"], title: str, build: Callable[[], Widget]) -> None:
        """Register a lazy builder for a section of Settings → AI."""
        ...

    def settings(self) -> Settings: ...

    def legacy_setting(self, key: Literal["ollama_host", "ai_models"]) -> Any:
        """PROPOSAL: read the old machine/project setting, or None. Local, cheap,
        read-only, with the old machine-over-project precedence; no migration
        writes in SlideMage. Only used once, before this plugin's migration marker."""
        ...

    def run_in_background(
        self, work: Callable[[], T], done: Callable[[T | None, BaseException | None], None],
    ) -> None:
        """Return immediately; work runs on a worker, done on the UI thread, even
        if the requesting page was destroyed. Scheduling failure raises before
        work starts. The plugin must ignore late widget updates."""
        ...

    def log(self, message: str) -> None:
        """Log with the plugin id. PROPOSAL: thread-safe for admin progress."""
        ...

    def show_message(self, text: str, error: bool = False) -> None: ...

    def try_start_ollama_task(self, what: str) -> bool:
        """PROPOSAL: take the app-wide Ollama slot, shared with core AI batches.
        False means busy (the host explains why). UI thread only."""
        ...

    def end_ollama_task(self) -> None:
        """PROPOSAL: release that slot, including after the page has gone away."""
        ...

    def Page(self) -> Widget:
        """A QWidget with a vertical body layout, changed() and destroyed signals."""
        ...

    def Field(self, label: str, widget: Widget) -> Widget: ...
    def caption(self, text: str, tone: Literal["", "error"] = "") -> Widget: ...
    def Button(self, text: str, *, compact: bool = False) -> Widget: ...
    def row(self, *widgets: Widget) -> Widget: ...
