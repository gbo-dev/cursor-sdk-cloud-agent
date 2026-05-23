# Issue -> PR Pipeline Agent (Concept Outline)

This document expands the "Issue -> PR" agent idea into a practical, non-technical outline. It is intended for teams that want an agent to take a work item from a tracker and reliably produce a reviewable pull request.

## Purpose

- Reduce lead time from "issue ready" to "PR ready".
- Standardize how work is interpreted, implemented, validated, and documented.
- Keep humans in control of prioritization, scope, and risk.

## Audience

- Engineers who will use the agent to ship small-to-medium changes.
- Tech leads who want consistent execution and predictable PR quality.
- New team members learning the codebase and delivery expectations.

## What The Agent Should Do

- Read an issue and gather missing context (requirements, constraints, expected behavior).
- Propose an implementation plan and get confirmation before making changes.
- Implement the change set with a clear, reviewable diff.
- Run the project's normal quality checks and report results.
- Open a PR that links back to the issue and explains the change.

## What The Agent Should Not Do

- Expand scope beyond the issue without explicit approval.
- Merge to main by default.
- Make risky refactors unless the issue explicitly calls for it.
- Bypass policies (tests, lint, security rules) for convenience.

## Inputs And Outputs

Inputs (minimum):

- Issue link or full issue text.
- Repo/branch context and any relevant environment constraints.

Outputs:

- A PR with a focused diff.
- A PR description that covers intent, approach, and how to validate.
- A short summary of checks run and their outcome.

## Workflow Stages (Recommended)

1. Intake
- Restate the problem in plain language.
- Identify acceptance criteria (explicit or implied).
- List unknowns and ask targeted questions.

2. Triage
- Confirm scope boundaries (what is in, what is out).
- Identify risk level (data migration, auth changes, billing, perf, security).
- Decide whether this is suitable for automation or requires human-led work.

3. Plan
- Produce a small, ordered plan.
- Call out tradeoffs and propose a default.
- Name the verification approach (tests, manual steps, logs to check).

4. Execution
- Make minimal, coherent changes.
- Keep commits/patches easy to review (avoid drive-by cleanup).
- Update or add tests where behavior changes.

5. Validation
- Run the standard checks for the repo.
- If failures occur, fix or escalate with a clear diagnosis.

6. PR Preparation
- Title: matches the issue intent.
- Description: what changed, why, how to verify, and any follow-ups.
- Link the issue and include a clear checklist for reviewers.

7. Handoff
- Recommend reviewers (by code ownership or domain).
- Flag anything that might surprise a reviewer (risk, migration steps, toggles).

## Human-In-The-Loop Checkpoints

- Before execution: confirm scope and plan.
- Before opening the PR: confirm the diff aligns with intent and no unrelated changes slipped in.
- For high-risk changes: require explicit approval to proceed at each stage.

## Good Practices For Reliable Agents

- Prefer clarification over assumption when requirements are ambiguous.
- Treat issue text as a hypothesis, not ground truth; validate against code and behavior.
- Keep outputs review-oriented: small diffs, strong explanations, reproducible validation.
- Default to the smallest correct change.
- Be explicit about uncertainty and what was inferred.

## Safety And Policy Guardrails

- Permissions: least privilege for repo access and external systems.
- Secrets: never print or commit secrets; avoid copying production data.
- Data changes: require extra review and provide rollback notes.
- External side effects (emails, payments, deletes): prefer safe modes and clear toggles.

## PR Quality Bar (What "Good" Looks Like)

- The PR description makes the intent obvious without reading the issue.
- Tests cover new behavior or updated edge cases.
- No unrelated formatting churn.
- Clear validation steps that a reviewer can follow quickly.
- Known limitations or follow-ups are listed explicitly.

## Education And Onboarding Uses

- Use the agent's plan output as a teaching artifact for how work is decomposed.
- Require the agent to cite where it learned key behavior (docs, tests, existing patterns).
- Encourage the agent to produce a short "reviewer map": files touched and why.

## Evaluation (Lightweight)

- Lead time: issue ready -> PR opened.
- Review churn: number of review cycles and categories of feedback.
- Regression rate: post-merge bug reports linked to agent-authored PRs.
- Trust signals: how often humans override plan or reject PRs.

## Common Failure Modes To Address

- Scope creep: agent fixes adjacent issues without permission.
- Overconfidence: agent ships without clarifying acceptance criteria.
- Shallow validation: agent runs checks but misses meaningful manual verification.
- Pattern mismatch: agent introduces a new approach instead of using established conventions.

## Suggested PR Template Sections

- Problem
- Solution
- Testing
- Risk / Rollback
- Notes for reviewers
