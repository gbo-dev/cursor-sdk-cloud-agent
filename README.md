# cursor-sdk-cloud-agent

GitHub Actions workflows powered by the [**Cursor Python SDK**](https://cursor.com/docs/sdk/python) — scriptable AI agents that run on the Actions runner, write code, run tests, and open PRs.

> Weekend project. Experimental. Use at your own curiosity.

## Workflows

### Issue → PR Pipeline

Assign an issue to yourself and a **local** Cursor SDK agent picks it up on the GitHub Actions runner: explores the codebase in plan mode, then switches to agent mode to implement the fix with a built-in test → iterate → verify loop, and opens a PR via `git` + `gh`.

**Trigger:** issue assigned to you.

## Setup (consumer repo)

```yaml
# .github/workflows/issue-to-pr.yml

permissions:
  contents: write
  issues: write
  pull-requests: write

on:
  issues:
    types: [assigned]

jobs:
  call:
    uses: gbo-dev/cursor-sdk-cloud-agent/.github/workflows/issue-to-pr-pipeline.yml@main
    secrets:
      CURSOR_API_KEY: ${{ secrets.CURSOR_API_KEY }}
```

Add `CURSOR_API_KEY` as a repo secret. Per the [Python SDK docs](https://cursor.com/docs/sdk/python), use a **user API key** from [Dashboard → Integrations](https://cursor.com/dashboard/integrations) or a **team service-account key** from [Team settings](https://cursor.com/dashboard/team-settings).

The workflow checks out the consumer repo on the runner and runs a **local** SDK agent (`local=LocalAgentOptions(cwd=...)`). The agent executes on the GitHub Actions machine against that checkout — it is not a Cursor-hosted cloud VM (`cloud=CloudAgentOptions(...)`). You still use Cursor’s **Composer model over the API**; “local” only describes where the process runs.

Optional env on the reusable workflow: `CURSOR_MODEL` (default `composer-2.5`), `CURSOR_MODEL_FAST` (default `"false"` for standard tier; set `"true"` for fast).
