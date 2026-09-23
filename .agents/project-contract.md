# Project Contract

## Repository and tracker

- Repository: `pong1013/statefulset-updater`; default branch: `main`.
- Specifications and tickets use GitHub Issues as configured in `docs/agents/issue-tracker.md`.

## Verification

- Run the checks from `.github/workflows/verify.yml`: Python syntax compilation for the three application entry points, `python3 -m unittest discover -s tests -v`, and Helm lint for all three charts with the CI values.
- For Kubernetes runtime claims, use an isolated test cluster and record the exact context, component image versions, deployment readiness, logs, and observed Kubernetes API behavior. A passing unit test or Helm render alone does not establish runtime success.
- Report each verification command and outcome. Mark runtime verification incomplete if the cluster, image build, deployment, or behavior check could not run; do not report it as passing.
- Report `HARNESS_VERIFICATION_STATUS` consistently with the actual verification evidence.

## Knowledge and work artifacts

- Follow `docs/agents/domain.md` for domain vocabulary and ADR discovery; create domain documents only when a decision is settled.
- Keep the canonical specification and implementation tickets in GitHub Issues. Do not create a second local specification or ticket copy.
- Keep disposable workflow checkpoints under `.agents/runs/` and ignore that directory in Git.

## Workspace and delivery

- Create feature work on a dedicated `codex/` branch or isolated worktree after the Workspace Gate. Preserve pre-existing work and keep unrelated files out of ticket commits.
- Use the repo's existing Python tests and Helm charts as the public verification seams. Add a runtime test path only when the approved specification calls for one.
- Commit only a ticket after its implementation, verification, and review pass. Push and open a pull request only after the Delivery Gate; do not merge or close issues as part of this workflow.
