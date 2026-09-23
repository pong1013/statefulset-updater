# Issue tracker: GitHub

Issues and specifications for this repository live in GitHub Issues. Use the `gh` CLI for operations.

## Repository identity

- **Repository:** `pong1013/statefulset-updater`
- **Configured from remote:** `origin`

Replace these values during setup. Never infer a target again when the configured repository and current remotes disagree; stop and ask the user to resolve the conflict. Do not store GitHub tokens or credentials in this file.

## Read operations

- Read an issue and comments with `gh issue view <number> --repo pong1013/statefulset-updater --comments` and request structured JSON fields when the caller needs exact metadata.
- List issues with `gh issue list --repo pong1013/statefulset-updater --state <state> --json ...` and use an explicit filter rather than assuming every open issue belongs to the Feature Run.
- Resolve a bare number as an issue or pull request before acting because GitHub shares one number space.

## Write operations

Every write requires the gate named by the calling workflow. Revalidate owner/repository and authentication immediately before acting.

- Create an issue with `gh issue create --repo pong1013/statefulset-updater --title <title> --body-file <file>`.
- Comment or edit only when that exact mutation was disclosed and approved.
- Never pass credentials on the command line or write them into body files.

## AI Workflow conventions

- The canonical specification is one GitHub parent issue.
- Implementation tickets are separate issues linked to the parent as native sub-issues when available.
- Create ticket issues blockers-first so dependency edges can reference existing identifiers.
- Prefer native GitHub issue dependencies. The dependency API uses the blocker's numeric database ID, not its displayed issue number or GraphQL node ID.
- When native sub-issues or dependencies are unavailable, use an explicit `Part of #<parent>` or `Blocked by: #<number>` body fallback and report the limitation.
- The ready frontier contains open child tickets with no open blockers. This release works one frontier ticket at a time in approved order.
- Do not apply labels in this release.
- Do not assign, close, or otherwise mutate issues merely because implementation passed.
- A pull request may contain closing references for the parent and tickets; they take effect only after merge. `$ai-workflow` never merges or directly closes them.

## When a Skill says "publish to the issue tracker"

Create the exact GitHub issue authorized by the current Specification or Ticket Breakdown Gate in the configured repository.

## When a Skill says "fetch the relevant ticket"

Read the exact issue, comments, parent/sub-issue relationship, and dependencies from the configured repository.
