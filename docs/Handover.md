# Ollama plugin: handover

Status, 2026-10-28: the plugin uses the API 1 additions in SlideMage's read-only `feature/ai-routes` worktree.
It is not yet loadable there as a replacement because core still registers an `ollama` route with the same ID.
The remaining core integration work and recommendations are in [PLAN.md](PLAN.md). Git was left to the
orchestrator. The manifest still says `>=10.0`, which does not by itself identify the unreleased additions.

## Implementation

`ollama/__init__.py` registers a route and one lazy settings section. `route.py` uses `RouteContext`, keeps an
asynchronous installed-model cache and notifies the host when choices change. `provider.py` uses public neutral
AI types and the plugin address or `legacy_ollama_host`; `http.py` keeps the local abortable transport. No
plugin module imports `slidemage.*` except `slidemage.plugin_api`.

`settings.py` acquires the host's shared route lease for update/delete, captures the address before running a
worker, delivers live update progress through the host to the UI log, and releases the lease after worker
completion even if the page was destroyed. An empty saved address clears the old override and falls back to
`OLLAMA_HOST` or localhost. Before the plugin setting exists, the legacy reader preserves the old effective
address without copying it into plugin storage. The host's current legacy reader can itself trigger core's
existing project-to-machine settings migration, despite the context's non-mutating contract. Tests use a structural fake host; its queued workers run
on worker threads, with progress and completion delivered on the test/UI thread.

The built-in Ollama route also delegates tasks, image/slide translation and chat to core's generic provider
adapters. This plugin does the same, so the new hook, terminal, custom chat surface, task-definition and
process-cancellation services do not need calls here. Local task IDs and labels are still necessary because
those route identifiers are not exported by the public API. See the plan for remaining core-only UI and loader
gaps.

## Verification

```sh
python3 build.py --check
PYTHONPATH=/home/juergen/github/SlideMage/.worktrees/ai-routes/src:. \
  QT_QPA_PLATFORM=offscreen XDG_CONFIG_HOME=$(mktemp -d) \
  /home/juergen/github/SlideMage/.venv/bin/python -m pytest -q
```

For SlideMage test support, put the branch's `tests` directory on `PYTHONPATH` or set `SLIDEMAGE_TESTS` to
`/home/juergen/github/SlideMage/.worktrees/ai-routes/tests` where a test uses it; this suite does not need it.
The HTTP fixture catches `PermissionError` and uses in-memory streams if sockets are forbidden. Those tests
exercise serialization, responses and abort signaling but not operating-system socket shutdown; rerun on a
TCP-enabled runner. Offscreen Qt widget tests run in this suite; no WebEngine tests are included. The existing
CI workflow still needs a pinned SlideMage source or release. No successful full loader integration is claimed.
