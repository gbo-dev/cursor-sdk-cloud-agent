#!/usr/bin/env python3
"""
Cursor SDK Agent Runner — Issue → PR Pipeline (async, self-verifying).

Triggered by GitHub Actions when an issue is assigned.
Uses Cursor's async SDK to run a local agent against the checked-out repo:
  1. Deep-explores the codebase and produces a plan (plan mode).
  2. Implements the fix with a test → iterate → verify loop (agent mode).
  3. Opens a PR via git + gh when all quality gates pass.

The agent runs on the GitHub Actions runner against the workspace checkout.
It can run tests, lint, and build in that environment. Prompts are structured
as a task-loop to prevent "first-guess-and-done" behaviour.

Environment variables (set by the workflow):
  CURSOR_API_KEY        — Cursor user or service-account API key
  GITHUB_TOKEN          — token for git push and gh pr create (GITHUB_TOKEN in GHA)
  GITHUB_REPOSITORY     — owner/repo  (e.g. "gbo-dev/my-project")
  ISSUE_NUMBER          — issue number to process
  ISSUE_TITLE           — issue title
  ISSUE_BODY            — issue body (GitHub-flavoured markdown)
  ISSUE_LABELS          — comma-separated labels (optional)
  STARTING_REF          — git ref checked out before the agent runs (default "main")
  MAX_RETRY_ATTEMPTS    — max test → fix retries in agent mode (default 3)
  CURSOR_MODEL          — model id (default "composer-2.5")
  CURSOR_MODEL_FAST     — "false" for standard tier (workflow sets this)
  AGENT_BRANCH          — branch the agent should commit on (set by workflow)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Allow imports when executed as a script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from model_config import build_model_selection, format_model
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from cursor_sdk import (
    AsyncClient,
    AgentOptions,
    CursorAgentError,
    LocalAgentOptions,
    SendOptions,
)
from cursor_sdk.events import TurnEndedUpdate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("agent_runner")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        log.error("Missing required environment variable: %s", name)
        sys.exit(1)
    return value


def load_ctx() -> dict:
    return {
        "repository": required_env("GITHUB_REPOSITORY"),
        "issue_number": required_env("ISSUE_NUMBER"),
        "title": required_env("ISSUE_TITLE"),
        "body": os.getenv("ISSUE_BODY", ""),
        "labels": [
            lbl.strip()
            for lbl in os.getenv("ISSUE_LABELS", "").split(",")
            if lbl.strip()
        ],
        "starting_ref": os.getenv("STARTING_REF", "main"),
        "max_retries": int(os.getenv("MAX_RETRY_ATTEMPTS", "3")),
        "branch": os.getenv("AGENT_BRANCH", ""),
    }


def build_issue_prompt(ctx: dict) -> str:
    parts = [
        f"Issue #{ctx['issue_number']}: {ctx['title']}",
        "",
        "## Issue Description",
        ctx["body"] or "(no description provided)",
    ]
    if ctx["labels"]:
        parts.extend(["", "Labels: " + ", ".join(ctx["labels"])])
    return "\n".join(parts)


def log_sdk_error(err: CursorAgentError) -> None:
    log.error("Cursor SDK error: %s", err)
    if err.request_id:
        log.error("Request ID: %s", err.request_id)
    if err.details:
        log.error("Error details: %s", err.details)
    if err.is_retryable:
        log.error("Retryable: yes (retry_after=%s)", err.retry_after)


def workspace_dir() -> str:
    return os.getcwd()


# ---------------------------------------------------------------------------
# token usage — accumulated from TurnEndedUpdate deltas
# ---------------------------------------------------------------------------


def _usage_int(usage: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = usage.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turns: int = 0

    def add_turn(self, usage: Mapping[str, Any] | None) -> None:
        if not usage:
            return
        self.turns += 1
        self.input_tokens += _usage_int(usage, "inputTokens", "input_tokens")
        self.output_tokens += _usage_int(usage, "outputTokens", "output_tokens")
        self.cache_read_tokens += _usage_int(
            usage, "cacheReadTokens", "cache_read_tokens"
        )
        self.cache_write_tokens += _usage_int(
            usage, "cacheWriteTokens", "cache_write_tokens"
        )

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def log_summary(self) -> None:
        if self.turns == 0:
            log.info("=== Session token usage ===")
            log.info("(no usage data received)")
            return
        log.info("=== Session token usage ===")
        log.info("Turns:              %s", self.turns)
        log.info("Input tokens:       %s", f"{self.input_tokens:,}")
        log.info("Output tokens:      %s", f"{self.output_tokens:,}")
        log.info("Cache read tokens:  %s", f"{self.cache_read_tokens:,}")
        log.info("Cache write tokens: %s", f"{self.cache_write_tokens:,}")
        log.info("Total tokens:       %s", f"{self.total_tokens:,}")


def make_on_delta(usage: TokenUsage):
    """Return an on_delta callback that accumulates per-turn token usage."""

    def on_delta(update) -> None:
        try:
            if isinstance(update, TurnEndedUpdate):
                usage.add_turn(update.usage)
        except Exception:
            log.exception("Failed to handle SDK delta: %r", update)

    return on_delta


# Log assistant text in coarse chunks (per speech segment), not per token.
_MAX_ASSISTANT_LOG_CHARS = 8000
_ASSISTANT_FLUSH_STATUSES = frozenset(
    {"FINISHED", "ERROR", "CANCELLED", "ABORTED", "FAILED"}
)


class _AssistantTextBuffer:
    """Accumulate streaming assistant deltas; flush on segment boundaries."""

    __slots__ = ("_parts",)

    def __init__(self) -> None:
        self._parts: list[str] = []

    def append(self, text: str) -> None:
        if text:
            self._parts.append(text)

    def flush(self) -> None:
        if not self._parts:
            return
        text = "".join(self._parts)
        self._parts.clear()
        text = text.strip()
        if not text:
            return
        if len(text) > _MAX_ASSISTANT_LOG_CHARS:
            log.info(
                "[assistant] %s... (%d chars total)",
                text[:_MAX_ASSISTANT_LOG_CHARS],
                len(text),
            )
        else:
            log.info("[assistant] %s", text)


# ---------------------------------------------------------------------------
# prompts
# ---------------------------------------------------------------------------


PLAN_SYSTEM_INSTRUCTION = """\
You are an expert software engineer analysing a GitHub issue to produce a
thorough implementation plan.

RULES (read-only — do NOT write code):

1. **Explore first.** Search the codebase to understand where the relevant
   code lives. Read related files, trace call paths, identify patterns used
   in similar parts of the project.

2. **Find the quality commands.** Locate the project's test runner and lint
   tool. Determine the exact shell commands to run the relevant test subset
   AND the full suite.

3. **Identify every affected file.** Map out which files need to be created,
   modified, or touched. Note shared types, config files, and docs.

4. **Propose a verification strategy.** List every command the agent must
   run before declaring the work complete. Commands must be copy-pasteable.

5. **Assess risk.** Call out data migrations, auth changes, breaking API
   surfaces, performance concerns, and regression risk.

6. **No code.** This is plan-mode — exploration and analysis only."""


def build_agent_prompt(ctx: dict) -> str:
    branch = ctx["branch"] or f"issue-{ctx['issue_number']}-agent"
    repo = ctx["repository"]
    max_retries = ctx["max_retries"]
    return f"""\
You are an autonomous software engineer. Implement the plan discussed above,
verify your work thoroughly, and open a PR — all without human intervention.

You are running locally on a GitHub Actions runner. The repository is already
checked out at the workspace root on branch `{branch}`.

---
## TASK LOOP — follow this exactly

### 1. DEEP CONTEXT LOAD
Before writing a single line:
- Re-read every file the plan says to change.
- Re-read the test files in the affected area.
- Note the exact style, naming conventions, and patterns used.

### 2. WRITE TESTS FIRST
- Write tests that prove the fix works BEFORE modifying production code.
- Run those tests — they should FAIL (confirming they catch the issue).

### 3. IMPLEMENT (minimal, focused diff)
- Make the smallest change set that satisfies the acceptance criteria.
- Do NOT refactor unrelated code, strip TODOs, or touch formatting.
- Match existing code style — copy nearby patterns, do not invent new ones.

### 4. VERIFY
- Run the project's lint command. Fix every issue it reports.
- Run the relevant test suite. Read every failure carefully.
- Once the targeted tests pass, run the FULL test suite.

### 5. ITERATE  (up to {max_retries} attempts)
If any check fails:
  a. Parse the error output — do not skim it.
  b. Fix the specific failure. Do not guess or rewrite unrelated code.
  c. Re-run the failing check.
  d. If it still fails, try a meaningfully different approach.
  e. If you exceed {max_retries} attempts without passing, STOP and document
     the outstanding failure in the PR description. Never silently ship
     broken code.

### 6. FINALISE
Proceed only when ALL quality gates are green:

  [ ] Lint clean
  [ ] Relevant tests pass
  [ ] Full suite passes (or a documented reason a subset was chosen)
  [ ] No unrelated file touched
  [ ] No TODOs, console.log, print(), or debugger statements remain
  [ ] No secrets or keys in the diff

### 7. COMMIT, PUSH, AND OPEN PR
Only after all checks pass:
- `git add` the intended files only.
- Commit with a conventional-commits message referencing issue #{ctx['issue_number']}.
- `git push -u origin HEAD`
- Open a PR with gh (GH_TOKEN is already set):

  gh pr create \\
    --repo {repo} \\
    --title "<concise title>" \\
    --body "<use PR template below>" \\
    --head {branch}

---
## PR DESCRIPTION TEMPLATE

Every PR you open must include:

### Problem
(concise restatement of the issue)

### Solution
(what changed and why — short)

### Testing
- [ ] Lint:  `...`  (exact command you ran)
- [ ] Unit tests: `...`
- [ ] Full suite: `...`

### Risk / Rollback
(note data migrations, auth, breaking changes — or "None")

### Reviewer Notes
(anything surprising, known limitations, or follow-ups)

---
## HARD CONSTRAINTS

- Do NOT merge — open a PR only.
- Do NOT add features outside the issue scope.
- Use conventional-commits style for commit messages.
- If uncertain about anything, state it explicitly in the PR description.
- Do NOT invent new conventions — follow what the codebase already does.
- Stay on branch `{branch}`; do not checkout other branches unless required for a fix.
"""


# ---------------------------------------------------------------------------
# async pipeline — single agent, two modes
# ---------------------------------------------------------------------------


async def stream_run(run) -> None:
    """Consume the live message stream and log progress."""
    assistant = _AssistantTextBuffer()
    async for msg in run.messages():
        if msg.type == "assistant":
            for block in msg.message.content:
                if block.type == "text" and block.text:
                    assistant.append(block.text)
        elif msg.type == "thinking":
            assistant.flush()
            log.debug("[thinking] %s", msg.text[:200])
        elif msg.type == "tool_call":
            assistant.flush()
            log.info(
                "[tool] %s  status=%s  args=%s",
                msg.name,
                msg.status,
                str(msg.args)[:200] if msg.args else "",
            )
        elif msg.type == "status":
            status = str(msg.status)
            if status in _ASSISTANT_FLUSH_STATUSES:
                assistant.flush()
            log.info("[status] %s: %s", status, getattr(msg, "message", ""))
    assistant.flush()


async def run_pipeline(ctx: dict) -> None:
    api_key = required_env("CURSOR_API_KEY")
    issue_prompt = build_issue_prompt(ctx)
    cwd = workspace_dir()
    session_usage = TokenUsage()

    log.info("Repository  : %s", ctx["repository"])
    log.info("Workspace   : %s", cwd)
    log.info("Issue       : #%s — %s", ctx["issue_number"], ctx["title"])
    log.info("Branch      : %s", ctx["branch"] or "(agent chooses)")
    log.info("Base ref    : %s", ctx["starting_ref"])
    model = build_model_selection(api_key)
    log.info("Model       : %s", format_model(model))
    log.info("Max retries : %s", ctx["max_retries"])

    try:
        await _run_pipeline(ctx, api_key, model, issue_prompt, cwd, session_usage)
    finally:
        session_usage.log_summary()


async def _run_pipeline(
    ctx: dict,
    api_key: str,
    model,
    issue_prompt: str,
    cwd: str,
    session_usage: TokenUsage,
) -> None:
    async with await AsyncClient.launch_bridge(workspace=cwd) as client:
        log.info("=== Phase 1/2: Planning (plan mode) ===")
        async with await client.agents.create(
            AgentOptions(
                model=model,
                api_key=api_key,
                local=LocalAgentOptions(cwd=cwd),
            ),
        ) as agent:
            log.info("Agent created: %s", agent.agent_id)

            plan_prompt = f"{PLAN_SYSTEM_INSTRUCTION}\n\n---\n\n{issue_prompt}"
            try:
                plan_run = await agent.send(
                    plan_prompt,
                    SendOptions(
                        mode="plan",
                        on_delta=make_on_delta(session_usage),
                    ),
                )
            except CursorAgentError as err:
                log_sdk_error(err)
                raise
            log.info("Plan run started: %s", plan_run.id)
            await stream_run(plan_run)

            plan_result = await plan_run.wait()
            log.info(
                "Plan finished  status=%s  duration=%sms",
                plan_result.status,
                plan_result.duration_ms,
            )
            if plan_result.status == "error":
                log.error("Plan run failed (run_id=%s)", plan_run.id)
                sys.exit(2)
            if plan_result.status not in ("finished",):
                log.error("Plan aborted (status=%s)", plan_result.status)
                sys.exit(1)

            log.info("=== Phase 2/2: Implement (agent mode) ===")
            agent_prompt = (
                "Now implement the plan you just produced.\n\n"
                f"{build_agent_prompt(ctx)}"
            )

            try:
                impl_run = await agent.send(
                    agent_prompt,
                    SendOptions(
                        mode="agent",
                        on_delta=make_on_delta(session_usage),
                    ),
                )
            except CursorAgentError as err:
                log_sdk_error(err)
                raise
            log.info("Implementation run started: %s", impl_run.id)
            await stream_run(impl_run)

            impl_result = await impl_run.wait()
            log.info(
                "Impl finished  status=%s  duration=%sms",
                impl_result.status,
                impl_result.duration_ms,
            )

            if impl_result.git:
                log.info(
                    "Git: branch=%s  commit=%s",
                    impl_result.git.branch,
                    impl_result.git.commit[:8]
                    if impl_result.git.commit
                    else "(none)",
                )

            if impl_result.status == "error":
                log.error("Implementation run failed (run_id=%s)", impl_run.id)
                sys.exit(2)
            if impl_result.status not in ("finished",):
                log.error("Implementation ended with status=%s", impl_result.status)
                sys.exit(1)

        log.info("=== Pipeline complete ===")


def main() -> None:
    try:
        asyncio.run(run_pipeline(load_ctx()))
    except CursorAgentError as err:
        log_sdk_error(err)
        sys.exit(1)


if __name__ == "__main__":
    main()
