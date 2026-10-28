# Ollama plugin: handover

Status, 2026-10-28: the plugin uses the real public API in SlideMage's `feature/ai-routes` worktree, but is
**not yet loadable as a replacement** on that branch. The loader sees the built-in `ollama` route and rejects
the duplicate. The other extraction gaps and recommendations are in [PLAN.md](PLAN.md). SlideMage's worktree
was read only; no commits were made here. The manifest still requires `>=10.0`.

## Implementation

`ollama/__init__.py` registers the route and AI section. `route.py` implements the public route with a
nonblocking plugin model cache. `provider.py` uses public neutral AI types; `http.py` retains the local
abortable transport. `settings.py` uses the host's settings and widgets plus Qt controls, captures the address
before worker operations, and ignores callbacks to a destroyed page. Plugin settings contain `ollama_host`;
per-task model choices belong to core. The removed draft migration depended on an unavailable `legacy_setting`
call. Core already handles old `local` model-choice IDs, but the old address needs a host migration API.

The administration guard is plugin-local, not shared with core AI batches. Its completion and failures are
logged on the UI thread; live update progress is unavailable through the current public host contract. The
settings section is registered via `add_settings_section`, and `settings_section()` returns `None` to avoid a
second copy in the current AI page. The branch's own model grid bypasses this route for the `ollama` ID, and
its settings page does not wire the plugin's changed signal to the built-in model grid.

`tests/fake_host.py` implements the public Host structurally: exercised calls record registrations, settings,
workers and UI messages; other public methods deliberately raise `NotImplementedError`. Tests import the real
neutral types. For additional SlideMage test support, put the branch's `tests` directory on `PYTHONPATH` or
set `SLIDEMAGE_TESTS` to `/home/juergen/github/SlideMage/.worktrees/ai-routes/tests` where a test uses it;
this suite currently needs neither.

## Verification

```sh
python3 build.py --check
PYTHONPATH=/home/juergen/github/SlideMage/.worktrees/ai-routes/src:. \
  QT_QPA_PLATFORM=offscreen XDG_CONFIG_HOME=$(mktemp -d) \
  /home/juergen/github/SlideMage/.venv/bin/python -m pytest -q
```

The check and 33 tests passed in the restricted sandbox. The existing CI workflow still needs a pinned
SlideMage source or release and currently cannot run this suite as configured. No test errors from forbidden sockets: the HTTP
fixture catches `PermissionError` and uses in-memory streams. This checks serialization, response handling and
abort signalling, but does not verify operating-system socket shutdown; run on a TCP-enabled runner for that.
A separate smoke check using the actual `PluginHost` confirmed route and section registration and confirmed
that the built-in route occupies the same ID. This does not constitute a successful full loader integration.
