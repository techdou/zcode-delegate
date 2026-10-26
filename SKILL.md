---
name: zcode-delegate
description: Delegate bounded work (coding, code review, analysis, writing, debugging) to the locally installed ZCode CLI as a headless sub-worker — mode mapping with least privilege, session continuity via --resume, provider cache stats from --json usage, timeout with process-tree kill. Use only when the user or parent agent explicitly asks for ZCode/zcode delegation, asks about the ZCode CLI as a worker, or asks to continue/resume a prior zcode delegation. Do not activate for generic coding, review, analysis, or writing tasks that do not name zcode.
license: MIT
compatibility: Requires the ZCode CLI (bundled inside ZCode Desktop at resources/glm/zcode.cjs — no standalone distribution as of 2026-10), Node.js, a logged-in Z.AI/BigModel account (shared with the desktop app), and Python 3.10+. Works on Windows; macOS/Linux untested.
metadata:
  version: "0.2.0"
---

# ZCode CLI delegation

Use the local ZCode CLI (`zcode`, headless `-p` mode) as a delegated worker. The parent agent owns routing, authorization, verification, session affinity, and the final user-facing answer.

Facts that shape this skill (measured 2026-10-26, CLI 0.17.0):

- The ZCode CLI ships **inside ZCode Desktop** (`<install>\resources\glm\zcode.cjs`); neither GitHub releases nor zcode.z.ai publish a standalone CLI. The wrapper auto-discovers the entry: `--entry` > `ZCODE_CLI_ENTRY` env > known desktop install locations per platform (Windows `%LOCALAPPDATA%\Programs\ZCode` / `C:\Program Files\ZCode` / custom drive letters, macOS `/Applications/ZCode.app`, Linux `/opt/ZCode`) > `zcode` on PATH. A launcher pair (`~/.local/bin/zcode` + `zcode.cmd`) uses the same candidate list.
- Headless `-p` mode defaults to `--mode yolo` (auto-approves every tool call). There is **no OS-level sandbox** — permission modes are approval gates, and in headless nobody approves. The wrapper maps modes so you must opt into privilege explicitly.
- `--json` prints `sessionId` + provider `usage` (incl. `cacheReadTokens`) + context projection — native support for session affinity and cache accounting.

## Quick reference

Minimal one-shot delegation (no session management):

```bash
python <skill-dir>/scripts/run.py \
  --mode <ask|review|edit|agent> \
  --cwd <workspace> \
  --task "<bounded task>"
```

Platform note: on hosts without a `python3` alias (common on Windows) invoke the script with `python`; everything else is identical.

Frequent switches: `--timeout <seconds>` (integer; default 1800), `--task-file <file>` (UTF-8, BOM tolerated), `--attach <path>` (repeatable), `--meta-output <file>`, `--dry-run`, `--extra "<raw CLI args>"` (pass-through, e.g. `--extra "--disallowed-tools,Bash"`).

Health and inventory:

```bash
python <skill-dir>/scripts/run.py --doctor
python <skill-dir>/scripts/run.py --version-info
python <skill-dir>/scripts/run.py --list-sessions
```

## Select the least-privileged work mode

The wrapper maps skill modes to ZCode permission modes:

- `ask` → `--mode plan` — answer/analyze/write text without touching files; writes are gated and headless cannot approve, so file changes are effectively blocked.
- `review` → `--mode plan` — inspect a project/material and return findings, no changes.
- `edit` → `--mode edit` — create/modify workspace files; auto-approves **file edits only** (measured: a delegated worker's attempt to run a Bash verification inside edit mode was still gated) — the parent agent verifies the diff.
- `agent` → `--mode yolo` — edits AND commands auto-approved. **Full trust on the host within that cwd; no sandbox.** Use a dedicated throwaway/dedicated `--cwd`, and consider `--extra "--disallowed-tools,Bash"` when command execution is not required.

Never escalate `agent` because a gated action was denied in `plan`/`edit` — that denial is the permission boundary working. Use `--extra` for controlled tool restriction instead.

## Startup weight and shared state

The headless child boots a **full agent runtime** (measured: ~30–90 s wall for a trivial first task; ~410k input tokens on turn 1, mostly system prompt + user plugins/skills). Consequences:

- Budget timeouts generously: ≥300 s for real tasks, more for `edit`/`agent`.
- The child loads the user's configured plugins and MCP servers; there is no `--no-mcp` switch in CLI 0.17.0. Startup noise and tool surface come with it.
- The child shares the **same login and quota** as ZCode Desktop, and reads the same user AGENTS.md — it may style itself like the user's assistant. The wrapper's stable contract prefix tells it that it is a delegated worker.
- No headless model selection in CLI 0.17.0 (`/model` is TUI-only; `--json` usage carries no model id). The worker runs on the account default model.

## Preserve continuity when the task continues

For related zcode turns in the same parent-agent conversation, use managed session affinity instead of fresh one-shots:

```bash
python <skill-dir>/scripts/run.py \
  --session auto \
  --session-key <stable-opaque-host-session-key> \
  --mode <ask|review|edit|agent> \
  --cwd <workspace> \
  --task "<bounded task>"
```

Generate/reuse one opaque `--session-key`, or rely on `ZCODE_SKILL_SESSION_KEY` / `FOREIGN_MODEL_SESSION_KEY` when the host provides one. Mappings are scoped by session key + resolved cwd, so different workspaces never share a zcode thread.

Session policy:

- `auto`: resume the mapped `sess_...` when present; otherwise create and record one.
- `continue`: require an existing mapping; fail rather than silently starting over.
- `new`: intentionally start a fresh session and replace the mapping.
- `off`: one-shot; registry untouched (use for unrelated work).

Measured on 0.17.0: `--resume` preserves context across processes (follow-up correctly recalled the prior turn), keeps the same `sessionId`, and reuses provider cache (~200k cached tokens on turn 2). The wrapper fails closed (exit 5) if a resumed run reports a different `sessionId` than the registry mapping.

Inspect/delete mappings with `--session-info`, `--list-sessions`, `--forget-session`. The registry (`state/registry.json`) stores metadata and token totals only — never task text or answers.

## Keep provider cache-friendly

The wrapper puts the invariant delegation contract before the changing task text, and managed resume reuses the native session — both maximize stable prompt prefixes. For accounting, add:

```bash
--meta-output .zcode-run-meta.json
```

and inspect `cache.cached_input_tokens` (provider-reported `cacheReadTokens`) and `cache.hit_ratio`. Do not claim a cache hit without the provider-reported usage this file carries.

## Measured pitfalls

- `--timeout` is **seconds**, an integer; budget minutes as seconds (`25m` → `1500`). On expiry the wrapper kills the whole child tree (`taskkill /T /F` on Windows) — no orphaned node processes.
- The CLI entry lives inside the desktop install — an uninstall, move, or desktop update can change the path/version silently. The wrapper discovers the entry per run (`--doctor` re-checks); `ZCODE_CLI_ENTRY` / `--entry` override discovery, and `NODE_EXE` overrides the node interpreter for `.cjs` entries.
- A trivial `ask` costs ~400k input tokens (mostly cached on repeats) — fine for real tasks, wasteful for "reply ok" smoke tests. Don't spam one-shot probes.
- Non-JSON stdout (CLI regression or crash mid-turn) exits 6 with the raw head printed — treat as failure, do not fabricate.
- `--mode plan` tasks that *need* to write will report blocked actions rather than hang — that is the read-only boundary; switch to `edit`/`agent` only when the task legitimately requires writes.
- Each headless run drops a `.video_agent/plugin_root` marker file (video-agent-kit plugin path pointer) into the `--cwd`. Harmless, but it pollutes the delegated workspace — expect it in diffs/cleanups; delegate into a dedicated directory when it matters.

## Delegation and verification rules

- Give the child the bounded task, constraints, desired output, and acceptance criteria; strip routing phrases such as "ask zcode to".
- Reuse a managed session only when the new turn is genuinely part of the same goal; start `new` for unrelated work to avoid context contamination.
- Keep credentials, tokens, private keys, and secrets out of prompts and artifacts.
- Treat non-zero wrapper exit codes (2 usage, 3 CLI failure, 4 timeout, 5 session mismatch, 6 bad output) as failure. Retry once only for a concrete safe fix.
- For `edit`/`agent`, inspect the workspace diff before claiming success.
- Do not recursively invoke the zcode-delegate skill from the delegated worker (the contract prefix already forbids it).

## Maintenance

No network updater in v0.1 — this skill is maintained by editing files directly. After a ZCode Desktop update, run `--doctor` and one tiny `--mode ask` task to re-verify `-p/--json/--resume` behavior before trusting session continuity.
