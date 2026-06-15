# agents.md Auto-Update Skill — Design Document

- Date: 2026-06-15
- Status: Draft (pending review)
- Repository: `async-agents-md-skill`
- Skill name (proposed): `async-agents-md`

## 1. Background and Goals

Build an AI skill that learns from **git `fix` commits** and **GitLab MR comments** to automatically maintain `agents.md` in the project root directory (the community-standard, project-level AI context document, similar to "robots.txt for agents").

Specifically, maintain two types of knowledge in `agents.md`:
- **Gotchas**: primarily sourced from `fix` commits
- **Conventions**: primarily sourced from MR review comments

Goal: capture the lessons the team accumulates through bug fixes and code reviews into `agents.md`, so subsequent AI agents don't repeat the same mistakes.

## 2. Scope

**In scope:**
- Incremental updates for a single repository on the current branch
- Extracting gotchas and conventions from git `fix` commits + GitLab MR comments
- Generating "proposed changes" to be written after human confirmation
- Generating a complete initial `agents.md` on cold start

**Out of scope (deferred, recorded as pending):**
- Cross-branch / multi-repo
- Missed or duplicate capture caused by `fix` commits being amended / rebased
- Non-review chatter in MR comments (filtered via semantic extraction, not specially handled)
- CI / hook automatic triggering (currently the skill is invoked manually)

## 3. Overall Architecture (Two Layers)

```
┌──────────────────────────────────────────────────────────┐
│  SKILL.md (orchestration layer / executed by Claude)      │
│  - Determine cold start vs incremental                    │
│  - Invoke script to fetch data; prefer MCP for MR,       │
│    fall back to script otherwise                          │
│  - Dispatch (single or multiple parallel) analysis        │
│    sub-agents to read cache and do semantic extraction    │
│  - Dedupe -> propose -> confirm -> write agents.md        │
└───────────────▲────────────────────────┬─────────────────┘
                │ structured JSON (cache) │ write agents.md (with marker)
┌───────────────┴────────────────────────▼─────────────────┐
│  scripts/agents_md.py (deterministic data + state layer,  │
│  stdlib only)                                             │
│  - marker read/write (embedded in HTML comment in         │
│    agents.md)                                             │
│  - git fix commit fetch (configurable pattern, diff       │
│    supported)                                             │
│  - MR comment fallback fetch (GitLab REST API + urllib +  │
│    token)                                                 │
│  - cache written to .agents-md-cache/                     │
└──────────────────────────────────────────────────────────┘
```

**Division of labor principle**: The script only handles "reproducible, testable" deterministic work (state, scope, fetching, caching); semantic judgment (whether something counts as a gotcha, whether it duplicates an existing entry, how to phrase it in the document) is delegated to Claude.

## 4. State / Marker Mechanism (marker embedded in agents.md)

**No standalone state file is used.** The marker is embedded as a single-line HTML comment at the end of `agents.md`:

```markdown
<!-- agents-md-state: {"schema":1,"last_commit":"abc1234","last_mr_updated_at":"2026-06-10T12:00:00Z","updated_at":"2026-06-15T09:00:00Z"} -->
```

**Fields:**
- `last_commit`: the git commit SHA processed up to last time
- `last_mr_updated_at`: the MR `updated_at` time processed up to last time (ISO8601)
- `updated_at`: the marker's own last-updated time

**Properties and guarantees:**
- **Shared with the document**: The marker enters the repository along with normal commits to `agents.md`, so the whole team automatically shares the same incremental starting point. No extra state file is needed, and it avoids introducing a sidecar that would cause frequent merge conflicts.
- **Advance only after confirmation**: The write flow is "confirm -> write content block -> invoke `state advance` to update the marker comment". Both steps happen **after user confirmation** and only touch `agents.md`. Therefore, if a proposal is rejected, neither step executes, the marker does not advance, and the relevant commit/MR will be processed again next time.
- **Single source of truth**: The format and read/write of the marker are handled entirely by the script's `state` subcommand; the skill never assembles this comment line directly.
- **Missing marker fallback**: If `agents.md` exists but the marker comment is missing (e.g. manually deleted), the script falls back to "treat the commit that last modified agents.md as the starting point" and emits a warning.

## 5. Data Modes (standard / deep)

When invoking the skill you may choose a mode; default is `standard`:

| Mode | Fetched content | Use case |
|---|---|---|
| `standard` (default) | commit title + body + raw MR comments | Day-to-day, lightweight, token-saving |
| `deep` | additionally fetches the diff of each fix commit | When you need to distill specific gotchas from "what changed" |

Both modes **cache to file first** before analysis (see Section 8).

## 6. Data Flow

### A. Cold start (bootstrap) — `agents.md` does not exist
1. Read `README.md` + directory structure + the most recent N commits (N configurable, default all)
2. Generate a complete `agents.md` (structure in Section 9); set the marker's `last_commit` to the current HEAD
3. Do not enter incremental analysis

### B. Incremental — `agents.md` already exists
1. The script reads the marker and fetches `fix` commits within range (`last_commit..HEAD`) filtered by pattern
2. MR: the skill first tries the GitLab MCP (filtering MRs updated since `last_mr_updated_at`); if MCP is unavailable -> invoke the script `mr gather` using a token as fallback
3. Fetch results are written to `.agents-md-cache/`
4. Dispatch analysis sub-agent(s) (single or multiple parallel) to read the cache and extract gotchas + conventions (Section 8)
5. Dedupe against existing `agents.md` -> propose -> confirm -> write -> advance marker (Section 10)

## 7. MR Source Strategy

**Prefer the GitLab MCP, with the script as fallback:**
1. The skill probes whether the GitLab MCP is available (attempt one list-MR capability call; success means available)
2. Available -> fetch via MCP the MRs updated since `last_mr_updated_at` and their comments; record the latest `updated_at` seen
3. Unavailable -> invoke `python scripts/agents_md.py mr gather --via api`; the script uses the token from the environment variable (default `GITLAB_TOKEN`) + urllib to call the GitLab REST API
4. Both unavailable -> skip MR, update based on git commits only (with a notice)

The GitLab instance URL is auto-inferred from the git remote by default and can be overridden via configuration.

## 8. Analysis Pipeline (cache -> sub-agent -> merge)

**Why cache to file before analysis**: Separate the gather phase (large volumes of raw git/MR output) from the analyze phase's context — the analysis sub-agent receives clean, compact cache files, uncontaminated by raw command output, yielding more stable extraction quality.

**Cache files** (located in `.agents-md-cache/`, gitignored, overwritten each run and retained until the next run for easy debugging):
- `commits.json`: `[{sha, message, body, files?, diff?}]` (`diff` only in deep mode)
- `mrs.json`: `[{iid, title, updated_at, comments: [...]}]`

**Execution: parallel multi-sub-agent + merge (implemented this iteration)**
- When the number of cache entries is <= `batch_size` (default 10), the skill dispatches a **single** analysis sub-agent that reads and analyzes everything
- When entries > `batch_size`, split into batches of `batch_size` each and dispatch **multiple parallel** analysis sub-agents (the `Agent` / `Task` tool, general-purpose, following the `dispatching-parallel-agents` pattern); each agent reads only its own batch, independently extracts gotchas / conventions, and returns structured JSON
- **Merge and dedupe**: After all batches return, the main flow merges all candidates, first doing **cross-batch deduplication** (different batches may distill similar entries), then semantic deduplication against the existing `agents.md` (Section 10)
- Concurrency limit follows the `dispatching-parallel-agents` guidance to avoid dispatching too many at once
- `analysis_mode` is configurable: `parallel` (default) / `sequential` (fall back to sequential single-agent when the environment is constrained or you want to save tokens)

## 9. agents.md Structure Template

```markdown
# Agents

> This file is maintained by the async-agents-md skill and records project context for AI agents.
> Sections marked "skill-maintained" are auto-updated by the skill; maintain other sections by hand.

## Overview
<generated on cold start; rarely touched afterwards>

## Build & Test
<build / test / lint commands>

## Code Layout
<key directories and module responsibilities>

## Conventions        <!-- skill-maintained: coding conventions / rules -->
- ...

## Gotchas            <!-- skill-maintained: gotchas / pitfalls -->
- ...

<!-- agents-md-state: {...} -->
```

- The skill **only writes** the `Conventions` and `Gotchas` sections; HTML comments delimit the block boundaries for precise replacement without touching hand-written content
- Other sections are maintained by the user after cold-start generation
- **Language**: first generation defaults to **English**; afterwards it follows the dominant language of the existing `agents.md`
- The file name defaults to `agents.md` (lowercase, community standard) and is configurable

## 10. Propose -> Confirm -> Write Flow

1. Analysis sub-agents return candidate entries (each carrying: source commit SHA / MR iid, classification gotcha/convention, one-line description)
2. The skill performs **semantic deduplication** of candidates against existing `Conventions`/`Gotchas`:
   - Brand-new entry -> mark "new"
   - Highly similar to an existing entry -> mark "likely duplicate" and let the user decide; do not auto-merge
   - Supplement / correction to an existing entry -> mark "modify"
3. The skill produces a **proposal list** (each item notes source, classification, action, and similarity to existing entries)
4. The user confirms item by item or selects all
5. After confirmation, the skill writes into the `Conventions`/`Gotchas` blocks of `agents.md`
6. It then immediately invokes `state advance` to update the trailing marker (new `last_commit` = current HEAD, new `last_mr_updated_at` = the latest MR time fetched this run). Both steps execute after confirmation, so rejection means no advance

## 11. Python Script Interface (`scripts/agents_md.py`)

Implemented with the standard library only (`urllib`, `json`, `subprocess` to call `git`, `argparse`), zero install.

```
state show   [--file agents.md]
    Print the current marker; if missing, print MISSING and fall back to "the commit that last modified agents.md"

git gather   [--pattern fix] [--mode standard|deep] [--file agents.md] [--out .agents-md-cache/commits.json]
    Read the marker and output JSON of commits matching pattern within last_commit..HEAD; deep mode includes diff

mr gather    --via api [--since <ts>] [--out .agents-md-cache/mrs.json]
    Token fallback: fetch JSON of MR comments updated since <ts>

state advance --commit <sha> [--mr <ts>] [--file agents.md]
    Update/insert the marker comment at the end of agents.md (call only after writing content)
```

All output is structured JSON for easy parsing by Claude. Exit codes: 0 success; non-zero accompanied by a human-readable error message.

## 12. Configuration

| Item | Default | Description |
|---|---|---|
| `mode` | `standard` | `standard` / `deep` |
| `pattern` | `fix` | fix-commit match pattern (regex for git log `--grep`) |
| `lookback` | `--all` | How many commits to look back on cold start (can be limited to N) |
| `language` | `en` | Language for first generation; afterwards follows the existing document |
| `target_file` | `agents.md` | Target file name |
| `gitlab_token_env` | `GITLAB_TOKEN` | Token environment variable name for MR fallback fetch |
| `gitlab_url` | auto-inferred | GitLab instance URL |
| `batch_size` | `10` | Batch size for batched analysis of large caches |
| `analysis_mode` | `parallel` | `parallel` (multi-batch parallel) / `sequential` (sequential single-agent) |
| `cache_dir` | `.agents-md-cache/` | Cache directory (gitignored) |

## 13. Repository File Layout

```
async-agents-md-skill/
├── README.md
├── LICENSE
├── SKILL.md                      # skill instructions (orchestration layer)
├── scripts/
│   └── agents_md.py              # deterministic data + state layer
├── .gitignore                    # ignores .agents-md-cache/
└── docs/superpowers/specs/
    └── 2026-06-15-agents-md-auto-update-skill-design.md   # this document
```

On install, `SKILL.md` + `scripts/` are copied to `~/.claude/skills/async-agents-md/`.

## 14. Boundaries and Limitations

- `fix` commits amended / rebased away -> may be missed or captured twice (YAGNI, not handled for now)
- Only supports a single repository on the current branch
- Non-review chatter in MRs is filtered semantically, not specially cleaned
- Manually deleting the marker comment -> falls back per the missing-marker fallback in Section 4
- Embedding the marker in the document means it must be committed along with `agents.md` to be shared across the team; if not committed locally, it only reflects local progress

## 15. Possible Future Enhancements (not in this iteration)

- `.agents-md.state.json` sidecar mode (as an alternative to embedded comments)
- CI / pre-push hook automatic triggering
- Cross-branch aggregation
