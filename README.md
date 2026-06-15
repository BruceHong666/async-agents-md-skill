# agents-md-keeper

[English](README.md) · [简体中文](README.zh-CN.md)

> A skill that learns from git `fix` commits and GitLab MR review comments, and incrementally maintains the `## Gotchas` and `## Conventions` sections of your project's `agents.md` (the community-standard AI context document).

---

## Why it's useful

Every bug fix and every code-review thread hides a hard-won lesson. If those lessons live only in commit history and MR comments, the next AI agent walks the same minefield again. `agents-md-keeper` distills those lessons out of `fix` commits and review comments, dedups them against what is already in `agents.md`, and proposes concrete edits for a human to approve.

Progress is tracked with an embedded marker, so each commit/MR is processed exactly once across runs — never re-hashed, never silently dropped.

## Architecture

Two clean layers, on purpose. Deterministic work stays in a reproducible script; semantic judgment stays with the orchestrating agent.

```
                 +-------------------------------+
   natural       |         SKILL.md              |   orchestration layer
   language  --> |  (executed by Claude)         |   - cold-start vs incremental
   trigger       |                               |   - dispatch analysis subagents
                 |  - decide cold-start/increment|   - dedup + propose + confirm
                 |  - call script                |   - write, then advance marker
                 +---------------+---------------+
                                 |
                                 v
                 +-------------------------------+
                 |    scripts/agents_md.py       |   deterministic data + state
                 |    (pure stdlib, 0 deps)      |   - marker parse / render
                 |                               |   - git commit gather
                 |                               |   - GitLab MR gather
                 |                               |   - cache + bootstrap
                 +-------------------------------+
```

- **`SKILL.md` — orchestration layer (executed by Claude).** Decides cold-start vs incremental, invokes the script, dispatches analysis subagents, merges + dedups their output, proposes a checklist, and writes — then advances the marker — only after the user accepts.
- **`scripts/agents_md.py` — deterministic data + state layer.** Single-file Python 3 standard library (argparse, json, re, subprocess, urllib, pathlib, datetime, os). Marker read/write, git commit gathering, GitLab MR gathering, caching, and cold-start bootstrap. Zero installs.

## Key features

- **Incremental tracking.** An HTML-comment marker is embedded at the end of `agents.md` and advances **only after the user accepts proposed changes**. A rejected proposal leaves the marker untouched, so the same commits/MRs are reconsidered next run.
- **MR source fallback.** GitLab MCP is preferred (richer, already-authenticated, no local token); the script's `urllib` client is the fallback, inferring the GitLab instance from the git remote and reading a token from the environment.
- **Parallel analysis subagents.** When data is large, items are split into batches of `batch_size`; one subagent per batch (following `dispatching-parallel-agents`), or a single sequential agent when concurrency is limited.
- **standard / deep modes.** `standard` reads commit title + body + MR comments; `deep` additionally reads each fix commit's diff for sharper gotchas (at higher token cost).
- **Cold start.** When no `agents.md` exists, the skill reads the README, the top-level directory tree, and recent commits, then composes a complete doc (`Overview`, `Build & Test`, `Code Layout`, `Conventions`, `Gotchas`) and stamps HEAD as the starting point.
- **Safe by construction.** The flow is propose → confirm → write, with a propose-only gate. The marker never advances before content is actually written.

## Install

Copy `SKILL.md` and the `scripts/` directory into your Claude skills folder:

```bash
mkdir -p ~/.claude/skills/agents-md-keeper \
  && cp SKILL.md scripts/agents_md.py ~/.claude/skills/agents-md-keeper/
```

> Note: keep the same directory layout so `SKILL.md` can find `scripts/agents_md.py` from the repo root.

## Usage

Trigger the skill from your project with natural language, e.g. *"update agents.md from recent fixes"*, *"refresh the AI context"*, or *"distill our review conventions"*. Configuration is passed as `key=value` tokens:

| Option          | Default       | Description                                                                                          |
| --------------- | ------------- | ---------------------------------------------------------------------------------------------------- |
| `mode`          | `standard`    | `standard` = commit title + body + MR comments; `deep` = also reads each fix commit's diff.          |
| `pattern`       | `^fix`        | Regex for fix commits (passed to `git log --grep -E`).                                               |
| `language`      | `en`          | First-run language. On later runs the skill matches the existing doc's dominant language.            |
| `target`        | `agents.md`   | Target file name.                                                                                    |
| `batch_size`    | `10`          | Items per analysis batch.                                                                            |
| `analysis_mode` | `parallel`    | `parallel` = one subagent per batch; `sequential` = single agent (saves tokens / avoids concurrency).|

The skill maintains exactly two sections: `## Conventions` and `## Gotchas`. All other sections are user-owned after cold start.

## CLI

`scripts/agents_md.py` exposes four subcommands. Run from the repo root.

```bash
python scripts/agents_md.py {git|mr|state|bootstrap} ...
```

| Subcommand                | Key flags                                                                                          | Purpose                                                        |
| ------------------------- | -------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `git gather`              | `--pattern`, `--mode {standard\|deep}`, `--file`, `--repo`, `--out`                               | Reads the marker, resolves `since`, emits matching commits.    |
| `mr gather`               | `--via api`, `--since`, `--repo`, `--out`, `--gitlab-token-env` (default `GITLAB_TOKEN`)          | Infers GitLab from the remote and fetches merged MR comments.  |
| `state show`              | `--file`, `--repo`                                                                                 | Prints the current marker (or a fallback `since`).             |
| `state advance`           | `--file`, `--commit`, `--mr`                                                                       | Advances the marker; run only after the user accepts changes.  |
| `bootstrap gather`        | `--repo`, `--limit`, `--out`                                                                       | Cold-start gather: README + top-level dirs + recent commits.   |

Defaults: caches are written under `.agents-md-cache/` (gitignored).

## Testing

```bash
pip install pytest && pytest
```

30 tests pass (see `tests/test_agents_md.py`).

## License

[MIT](LICENSE) © 2026 BruceHong
