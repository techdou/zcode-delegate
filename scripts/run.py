#!/usr/bin/env python3
"""zcode-delegate wrapper — run bounded tasks in a local ZCode CLI headless worker.

The ZCode CLI ships inside the ZCode Desktop app (resources/glm/zcode.cjs); there is
no standalone distribution channel as of 2026-10. This wrapper auto-discovers the
entry (explicit flag > env > known desktop install locations per platform > PATH),
maps skill modes to CLI permission modes, enforces timeouts with process-tree kill,
keeps a local session registry for --resume continuity, and parses the CLI's --json
result (sessionId + provider usage incl. cached tokens).

Exit codes: 0 ok, 2 usage/config, 3 CLI non-zero, 4 timeout, 5 session mismatch,
6 unparseable output.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REGISTRY_PATH = SKILL_DIR / "state" / "registry.json"

# Known desktop-install candidates per platform. The CLI lives at
# <install>/resources/glm/zcode.cjs inside every desktop build observed so far.
def desktop_entry_candidates() -> list[str]:
    cands: list[str] = []
    localappdata = os.environ.get("LOCALAPPDATA", "")
    if localappdata:
        cands.append(os.path.join(localappdata, "Programs", "ZCode",
                                  "resources", "glm", "zcode.cjs"))  # win per-user (electron-builder default)
    cands += [
        r"C:\Program Files\ZCode\resources\glm\zcode.cjs",            # win per-machine
        "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs",  # macOS
        "/opt/ZCode/resources/glm/zcode.cjs",                        # linux deb/rpm
        os.path.expanduser("~/ZCode/resources/glm/zcode.cjs"),       # portable layout
    ]
    # Machine-specific installs seen on this host series (kept last, harmless elsewhere).
    cands += [r"E:\ZCode\resources\glm\zcode.cjs"]
    return cands

MODE_TO_ZCODE = {
    "ask": "plan",    # read-only analysis; writes are gated and headless cannot approve
    "review": "plan",
    "edit": "edit",
    "agent": "yolo",  # full trust: edits AND commands auto-approved, no OS sandbox
}

INVARIANT_CONTRACT = (
    "[DELEGATED WORKER CONTRACT - stable prefix]\n"
    "You are a delegated headless worker subprocess, spawned by a parent agent to "
    "finish one bounded task. Rules:\n"
    "1. Execute the TASK below as your whole assignment; answer directly in the "
    "language of the task.\n"
    "2. NEVER load or invoke delegation skills (zcode-delegate, codex-delegate, "
    "gemini-delegate) and never spawn nested agents for delegation.\n"
    "3. Do not ask the user questions; if key information is missing, state your "
    "assumptions explicitly and proceed.\n"
    "4. Keep secrets, tokens and private keys out of prompts, logs and artifacts.\n"
    "5. Stay within the provided working directory unless the task explicitly "
    "requires otherwise.\n"
    "[END CONTRACT]\n\n"
    "[TASK]\n"
)


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def load_registry() -> dict:
    if REGISTRY_PATH.exists():
        try:
            data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"schema": 1, "sessions": {}}


def save_registry(reg: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def scope_key(session_key: str, cwd: str) -> str:
    return f"{session_key}::{Path(cwd).resolve()}"


def find_node() -> str | None:
    for cand in [os.environ.get("NODE_EXE"), shutil.which("node")]:
        if cand and Path(cand).exists():
            return cand
    return None


def resolve_entry(explicit: str | None) -> tuple[list[str], str, str]:
    """Return (command prefix, entry path for reporting, source description).

    Precedence: --entry > ZCODE_CLI_ENTRY > known desktop install candidates >
    `zcode` on PATH (launcher script or a future standalone CLI).
    """
    entry: str | None = None
    source = ""
    if explicit:
        entry, source = explicit, "--entry"
    elif os.environ.get("ZCODE_CLI_ENTRY"):
        entry, source = os.environ["ZCODE_CLI_ENTRY"], "ZCODE_CLI_ENTRY"
    if entry:
        if not Path(entry).exists():
            sys.exit(f"[error] entry does not exist: {entry} (source: {source})")
        entry = str(Path(entry))
        if entry.endswith((".cjs", ".js", ".mjs")):
            node = find_node()
            if not node:
                sys.exit("[error] node not found (PATH or NODE_EXE); required for a .cjs CLI entry")
            return [node, entry], entry, source
        return [entry], entry, source

    for cand in desktop_entry_candidates():
        if cand and Path(cand).exists():
            node = find_node()
            if not node:
                sys.exit("[error] node not found (PATH or NODE_EXE); required for a .cjs CLI entry")
            return [node, cand], cand, f"desktop install ({cand})"

    found = shutil.which("zcode")
    if found:
        if found.lower().endswith((".cmd", ".bat")):
            return ["cmd", "/c", found], found, "PATH launcher"
        return [found], found, "PATH"

    sys.exit(
        "[error] no zcode CLI entry found. Install ZCode Desktop (CLI ships inside "
        "it at resources/glm/zcode.cjs), put a zcode launcher on PATH, or set "
        "ZCODE_CLI_ENTRY to the entry path."
    )


def cli_version(explicit: str | None) -> str:
    prefix, _, _ = resolve_entry(explicit)
    try:
        out = subprocess.run(prefix + ["--version"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        text = (out.stdout or out.stderr or "").strip()
        return text.splitlines()[0] if text else "(no output)"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return f"(failed: {exc})"


def extract_json(raw: str) -> dict:
    text = raw.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost {...} object (stdout may carry leading log lines).
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("cannot parse --json output")


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # whole session (setsid)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zcode-delegate run.py",
                                description="Delegate one bounded task to the local ZCode CLI headlessly.")
    p.add_argument("--mode", choices=sorted(MODE_TO_ZCODE),
                   help="ask/review=read-only(plan), edit=workspace edits, agent=edits+commands(yolo)")
    p.add_argument("--cwd", help="working directory for the worker (required for task runs)")
    p.add_argument("--task", help="bounded task text")
    p.add_argument("--task-file", help="read task text from a UTF-8 file (BOM tolerated)")
    p.add_argument("--attach", action="append", default=[],
                   help="attach a local file to the prompt; repeatable")
    p.add_argument("--timeout", type=int, default=1800,
                   help="hard timeout in SECONDS (integer; budget minutes as seconds)")
    p.add_argument("--session", choices=["auto", "continue", "new", "off"], default="off",
                   help="managed --resume affinity policy")
    p.add_argument("--session-key",
                   help="opaque host-session key (default: $ZCODE_SKILL_SESSION_KEY / "
                        "$FOREIGN_MODEL_SESSION_KEY / 'default')")
    p.add_argument("--entry", help="explicit zcode CLI entry path (.cjs or executable)")
    p.add_argument("--extra", action="append", default=[],
                   help="raw pass-through args appended to the zcode command; each --extra "
                        "value may itself be a comma-separated group, e.g. "
                        "--extra \"--disallowed-tools,Bash\"")
    p.add_argument("--meta-output", help="write run metadata (usage/cache/session) to this JSON file")
    p.add_argument("--dry-run", action="store_true", help="print the assembled command and exit")
    p.add_argument("--doctor", action="store_true", help="environment/CLI health check, no model call")
    p.add_argument("--version-info", action="store_true", help="print CLI version, exit")
    p.add_argument("--list-sessions", action="store_true", help="list registry mappings")
    p.add_argument("--session-info", action="store_true", help="show the mapping for this key/cwd")
    p.add_argument("--forget-session", action="store_true", help="delete the mapping for this key/cwd")
    return p


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    args = build_parser().parse_args()

    if args.version_info:
        print(cli_version(args.entry))
        return

    if args.doctor:
        prefix, entry, source = resolve_entry(args.entry)
        node = find_node()
        print(f"python: {sys.version.split()[0]} ({sys.executable})")
        print(f"entry: {entry}  [source: {source}]")
        print(f"launch: {' '.join(prefix)}")
        print(f"node: {node or '(not needed for a non-.cjs entry)'}")
        print(f"cli version: {cli_version(args.entry)}")
        print(f"registry: {REGISTRY_PATH} ({'present' if REGISTRY_PATH.exists() else 'absent'})")
        print("login: cannot verify without a model call; run a tiny ask task to confirm")
        return

    session_key = (args.session_key
                   or os.environ.get("ZCODE_SKILL_SESSION_KEY")
                   or os.environ.get("FOREIGN_MODEL_SESSION_KEY")
                   or "default")
    reg = load_registry()
    reg_key = scope_key(session_key, args.cwd) if args.cwd else None

    if args.list_sessions:
        rows = reg["sessions"].values()
        if not rows:
            print("(no registered sessions)")
        for row in rows:
            print(f"{row['session_key']} :: {row['cwd']}\n  id={row['session_id']} "
                  f"runs={row.get('runs', '?')} last={row.get('last_used_at', '?')}")
        return

    if args.forget_session:
        if reg_key and reg["sessions"].pop(reg_key, None) is not None:
            save_registry(reg)
            print(f"forgot {reg_key}")
        else:
            print("(nothing to forget for this key/cwd)")
        return

    if args.session_info:
        row = reg["sessions"].get(reg_key) if reg_key else None
        print(json.dumps(row, ensure_ascii=False, indent=2) if row else "(no mapping)")
        return

    # ---- task run ----
    if not args.cwd:
        sys.exit("[error] --cwd is required for task runs")
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        sys.exit(f"[error] --cwd is not a directory: {cwd}")

    if args.task_file:
        task = Path(args.task_file).read_text(encoding="utf-8-sig")
    elif args.task is not None:
        task = args.task
    else:
        sys.exit("[error] provide --task or --task-file")
    if not task.strip():
        sys.exit("[error] task text is empty")

    for path in args.attach:
        if not Path(path).exists():
            sys.exit(f"[error] --attach file not found: {path}")

    expected_id = reg["sessions"].get(reg_key, {}).get("session_id") if reg_key else None
    if args.session == "continue" and not expected_id:
        sys.exit(f"[error] session policy 'continue' but no mapping for {reg_key}; "
                 "use --session auto or new")

    prefix, entry, source = resolve_entry(args.entry)
    cmd = prefix + ["-p", INVARIANT_CONTRACT + task,
                    "--cwd", str(cwd),
                    "--mode", MODE_TO_ZCODE[args.mode],
                    "--json", "--no-color"]
    for path in args.attach:
        cmd += ["--attach", path]
    if args.session in ("auto", "continue") and expected_id:
        cmd += ["--resume", expected_id]
    for raw in args.extra:
        cmd += raw.split(",") if "," in raw else [raw]

    if args.dry_run:
        print(" ".join(cmd))
        return

    eprint(f"[zcode-delegate] mode={args.mode}->{MODE_TO_ZCODE[args.mode]} "
           f"session={args.session}{'(resume ' + expected_id + ')' if (args.session in ('auto', 'continue') and expected_id) else ''} "
           f"timeout={args.timeout}s entry=[{source}]")
    started = time.monotonic()
    popen_kwargs = {"cwd": str(cwd), "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                    "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True  # own process group for tree kill
    try:
        proc = subprocess.Popen(cmd, **popen_kwargs)
    except OSError as exc:
        sys.exit(f"[error] failed to launch CLI: {exc}")
    try:
        out, err = proc.communicate(timeout=max(30, args.timeout))
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        eprint(f"[error] timed out after {args.timeout}s; process tree killed")
        sys.exit(4)

    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        eprint(f"[error] CLI exited {proc.returncode}\n{err[:2000]}")
        sys.exit(3)

    try:
        result = extract_json(out)
    except ValueError:
        eprint(f"[error] unparseable --json output (exit 6); stdout head:\n{out[:1500]}")
        sys.exit(6)

    session_id = result.get("sessionId")
    if (args.session in ("auto", "continue") and expected_id
            and session_id and session_id != expected_id):
        eprint(f"[error] session mismatch: expected {expected_id}, got {session_id} "
               "(fail closed; registry untouched)")
        sys.exit(5)

    usage = result.get("usage") or {}
    response = result.get("response") or ""
    print(response if response.strip() else "(empty response)")

    if args.session in ("auto", "new") and reg_key and session_id:
        row = reg["sessions"].setdefault(reg_key, {
            "session_key": session_key, "cwd": str(cwd), "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat()})
        if session_id != row.get("session_id"):
            row["session_id"] = session_id  # policy new: replaced mapping
        row["last_used_at"] = datetime.now(timezone.utc).isoformat()
        row["runs"] = row.get("runs", 0) + 1
        row["last_mode"] = args.mode
        totals = row.setdefault("usage_totals", {})
        for field in ("inputTokens", "outputTokens", "cacheReadTokens"):
            totals[field] = totals.get(field, 0) + (usage.get(field) or 0)
        save_registry(reg)

    if args.meta_output:
        input_tokens = usage.get("inputTokens") or 0
        cached = usage.get("cacheReadTokens") or 0
        meta = {
            "ok": True, "exit_code": 0, "elapsed_sec": round(elapsed, 1),
            "mode": args.mode, "zcode_mode": MODE_TO_ZCODE[args.mode],
            "session_policy": args.session, "session_id": session_id,
            "resumed": bool(args.session in ("auto", "continue") and expected_id),
            "response_chars": len(response),
            "usage": usage,
            "cache": {
                "cached_input_tokens": cached,
                "hit_ratio": round(cached / input_tokens, 4) if input_tokens else None,
            },
            "projection": result.get("projection"),
        }
        Path(args.meta_output).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
