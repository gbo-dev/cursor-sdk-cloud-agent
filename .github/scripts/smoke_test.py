#!/usr/bin/env python3
"""
SDK smoke tests for the local-agent pipeline.

Usage:
  export CURSOR_API_KEY="..."
  python .github/scripts/smoke_test.py          # quick: one minimal run
  python .github/scripts/smoke_test.py --full   # plan + agent shape (like pipeline)

Runs from the repo root — same local runtime as agent_runner.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from cursor_sdk import (
    AgentOptions,
    AsyncClient,
    CursorAgentError,
    LocalAgentOptions,
    SendOptions,
)

MODEL = os.getenv("CURSOR_MODEL", "composer-2.5")


def require_api_key() -> str:
    key = os.getenv("CURSOR_API_KEY", "").strip()
    if not key:
        print("Set CURSOR_API_KEY", file=sys.stderr)
        sys.exit(1)
    return key


def log_error(label: str, err: CursorAgentError) -> None:
    print(f"FAIL [{label}]: {err}", file=sys.stderr)
    if err.request_id:
        print(f"  request_id: {err.request_id}", file=sys.stderr)
    if err.details:
        print(f"  details: {json.dumps(err.details, indent=2)}", file=sys.stderr)


async def try_send(
    client,
    api_key: str,
    *,
    label: str,
    send_mode: str | None,
    prompt: str,
    agent=None,
) -> tuple[bool, object | None]:
    print(f"\n=== {label} ===")
    print(f"model={MODEL!r} send_mode={send_mode!r}")
    send_opts = SendOptions(mode=send_mode) if send_mode else None
    try:
        if agent is None:
            async with await client.agents.create(
                AgentOptions(
                    model=MODEL,
                    api_key=api_key,
                    local=LocalAgentOptions(cwd=os.getcwd()),
                ),
            ) as new_agent:
                print(f"Agent created: {new_agent.agent_id}")
                run = await new_agent.send(prompt, send_opts)
                print(f"Run started: {run.id}")
                result = await run.wait()
                print(
                    f"Run finished: status={result.status} "
                    f"duration_ms={result.duration_ms}"
                )
                if result.result:
                    preview = result.result[:200].replace("\n", " ")
                    print(f"Result preview: {preview!r}")
                return result.status == "finished", None

        print(f"Agent reused: {agent.agent_id}")
        run = await agent.send(prompt, send_opts)
        print(f"Run started: {run.id}")
        result = await run.wait()
        print(
            f"Run finished: status={result.status} duration_ms={result.duration_ms}"
        )
        if result.result:
            preview = result.result[:200].replace("\n", " ")
            print(f"Result preview: {preview!r}")
        return result.status == "finished", agent
    except CursorAgentError as err:
        log_error(label, err)
        return False, agent


async def run_quick(client, api_key: str) -> None:
    ok, _ = await try_send(
        client,
        api_key,
        label="local agent, minimal prompt",
        send_mode=None,
        prompt="Reply with exactly: smoke ok",
    )
    if not ok:
        sys.exit(1)
    print("\nQuick smoke test passed.")


async def run_full(client, api_key: str) -> None:
    ok, _ = await try_send(
        client,
        api_key,
        label="local agent, minimal prompt",
        send_mode=None,
        prompt="Reply with exactly: smoke ok",
    )
    if not ok:
        sys.exit(1)

    async with await client.agents.create(
        AgentOptions(
            model=MODEL,
            api_key=api_key,
            local=LocalAgentOptions(cwd=os.getcwd()),
        ),
    ) as agent:
        ok, _ = await try_send(
            client,
            api_key,
            label="pipeline shape: plan at send",
            send_mode="plan",
            prompt=(
                "Briefly outline how you would explore this repository. "
                "Do not write or edit any files."
            ),
            agent=agent,
        )
        if not ok:
            sys.exit(1)

        ok, _ = await try_send(
            client,
            api_key,
            label="pipeline shape: agent follow-up",
            send_mode="agent",
            prompt=(
                "Do not change any files. Reply with exactly: agent phase ok"
            ),
            agent=agent,
        )
        if not ok:
            sys.exit(1)

    print("\nFull smoke test passed (minimal + plan + agent).")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Local SDK smoke tests")
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run plan → agent follow-up (matches pipeline shape)",
    )
    args = parser.parse_args()

    api_key = require_api_key()
    cwd = os.getcwd()
    print(f"Workspace: {cwd}")
    print(f"Model: {MODEL}")

    async with await AsyncClient.launch_bridge(workspace=cwd) as client:
        if args.full:
            await run_full(client, api_key)
        else:
            await run_quick(client, api_key)


if __name__ == "__main__":
    asyncio.run(main())
