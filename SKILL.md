---
name: async-agents-md
description: Update the project's agents.md by learning from recent `fix` commits and merge-request review comments — extracting gotchas and coding conventions, deduping them against the existing doc, and proposing changes for the user to approve. Use this whenever the user wants to update agents.md, refresh or maintain the project's AI context doc, capture lessons learned from recent fixes, distill conventions out of MR/code-review comments, maintain the Gotchas/Conventions sections, or onboard a newly added AI agent with up-to-date project notes. Trigger this skill even when the user never literally says "agents.md" but expresses any of those intents (for example "capture what we learned from last week's fixes", "refresh the AI context", "distill our review conventions", "prepare project notes for the new agent").
---

# agents.md updater

You maintain the project's `agents.md` — the community-standard, project-level AI
context document. You extract **gotchas** (mostly from `fix` commits) and **coding
conventions** (mostly from MR review comments), dedup them against what is already in
the doc, and propose changes for the user to approve.

Why this division of labor matters: deterministic work (parsing markers, gathering
commits/MRs, writing cache files, rendering the marker) is done by a stdlib-only script
so it is reproducible and testable; semantic judgment (is this actually a gotcha? does it
duplicate an existing bullet? how should it be phrased?) stays with you. Keep those
roles separate — never hand-edit the marker line by hand, and never let the script make
semantic calls.

Two sections are skill-maintained: `## Conventions` and `## Gotchas`. Everything else
(`## Overview`, `## Build & Test`, `## Code Layout`) is user-owned after cold start — you
generate it once, then leave it alone. If you are updating an existing doc that predates
this skill and lacks these two sections, create them on the first incremental run (see
"Apply"). An HTML-comment marker at the end of `agents.md` records incremental progress.
It advances **only after the user accepts proposed changes**, so a rejected proposal
leaves the marker untouched and the same commits/MRs get reconsidered next time.

## Setup

The data/state layer is `scripts/agents_md.py` (Python 3 stdlib only, zero installs). It
ships inside **this skill's directory**, not the target repo — so locate it before running:

- Call it by its skill-directory path, e.g.
  `python ~/.claude/skills/async-agents-md/scripts/agents_md.py <subcommand>`.
- Or copy it into the target repo once if you want it tracked:
  `cp <skill-dir>/scripts/agents_md.py scripts/agents_md.py`, then call
  `python scripts/agents_md.py <subcommand>` from the repo root.

Every `agents_md.py ...` command below means whichever path you resolved above. Cache files
are written under `.agents-md-cache/` in the target repo (gitignored; overwritten each run,
kept for debugging).

## Configuration

Parse `$ARGUMENTS` as `key=value` tokens. Defaults:

- `mode`: `standard` (default) — commit title + body + MR comments. `deep` also reads each
  fix commit's diff, which costs tokens but yields sharper gotchas when the fix matters
  more than the message.
- `pattern`: regex for fix commits, default `^fix` (passed to `git log --grep -E`).
- `language`: the **written/save** language for cold start, default `en`. On later runs
  the written language follows the existing doc's dominant language instead. This does
  NOT control your conversation or proposal-preview language — see "Output language".
- `target`: file name, default `agents.md`.
- `batch_size`: items per analysis batch, default `10`.
- `analysis_mode`: `parallel` (default) | `sequential` (fall back to sequential single-agent
  when the environment limits concurrency or you want to save tokens).
- `max_commits`: when there is no marker (first run, or marker missing), cap the scan to the
  most recent N matching commits — default `100` — so you don't scan the entire history.
  `0` means unlimited. Older history is skipped by design (the skill prioritizes recent
  lessons). Ignored when a marker exists, since the incremental window is already bounded.

## Procedure

### 1. Decide cold-start vs incremental

- If `target` does NOT exist → go to **Bootstrap**.
- If it exists → go to **Incremental**.

### 2. Bootstrap (cold start)

You have no marker to build on, so synthesize a full doc from what the repo already tells
you, then stamp the current HEAD as the starting point.

1. Run:
   `python scripts/agents_md.py bootstrap gather --repo . --out .agents-md-cache/bootstrap.json`
2. Read `.agents-md-cache/bootstrap.json` (README text + top-level dirs + recent commits).
3. Compose a full `agents.md` in the configured `language` using this structure:
   - `# Agents` + a one-line purpose note
   - `## Overview`
   - `## Build & Test` — infer from the README and common files (package manifests, CI
     configs, Makefile). Only leave a TODO if a command is genuinely undiscoverable.
   - `## Code Layout` — key directories and module responsibilities from the top-level tree.
   - `## Conventions` — start empty (or with obvious items)
   - `## Gotchas` — start empty
4. Write the file WITHOUT the marker, then advance the marker onto it:
   `python scripts/agents_md.py state advance --file agents.md --commit $(git rev-parse HEAD)`
5. Show the user the generated file. Done — cold start does not run the proposal step.

### 3. Incremental

This is the steady-state flow: gather → analyze → dedup → propose → confirm → write →
advance marker. Order matters, because the marker should only move forward once content is
actually written.

1. **Gather git data.** The script reads the marker, resolves a `since` commit, and emits
   matching commits in `last_commit..HEAD`:
   `python scripts/agents_md.py git gather --file agents.md --pattern <pattern> --mode <mode> --max-commits <max_commits> --out .agents-md-cache/commits.json`
   Note the `head` SHA printed on stdout — you will need it when advancing the marker. If the
   output includes `"note": "no marker; capped to ..."`, tell the user older history was
   skipped and offer `--max-commits 0` for a one-time full scan.
2. **Gather MR data.** Prefer the GitLab MCP when it is available (probe it once by listing
   MRs updated after the marker's `last_mr_updated_at`, then fetch their non-system
   comments) — MCP gives richer, already-authenticated data without a local token. If the
   MCP is not available, fall back to the script, which infers the GitLab instance from the
   git remote and uses a token from the environment:
   `python scripts/agents_md.py mr gather --via api --repo . --out .agents-md-cache/mrs.json`
   Note the `last_mr_updated_at` printed on stdout. If neither source is available, skip MR
   (proceed on git data only) and tell the user.
3. **Analyze the cache.** First read the existing `## Conventions` and `## Gotchas` bullets
   from `agents.md` — dedup needs them in view, not just in memory — and pass them to each
   analysis subagent along with its cache chunk. Dispatch subagents — one per `batch_size`
   chunk, parallel by default following `dispatching-parallel-agents` (or a single sequential
   agent if `analysis_mode=sequential`, or if total items ≤ `batch_size`). Each subagent
   reads its chunk from `.agents-md-cache/commits.json` and `.agents-md-cache/mrs.json` and
   returns JSON:
   `{"gotchas": [{"content": "...", "scope": "project|general", "source": "<sha/iid>", "similar_to": "..."}], "conventions": [ ...same shape... ]}`
   - `scope`: `general` = reusable on any project (e.g. "SSE needs heartbeat + flushHeaders");
     `project` = specific to this codebase (e.g. "this repo's DB URL is hardcoded"). Tagging
     it lets the user keep general rules and drop project-specific noise.
   - `similar_to`: compare every item against the existing bullets you passed in. Fill it
     with the first ~12 words of the existing bullet it restates — even loosely (a
     "fullWidth" finding echoes a "no native elements" rule) — and leave it empty only if
     genuinely novel. This structural anchor catches duplicates that pure semantic
     guesswork misses.
4. **Merge + dedup.** Combine all subagents' outputs; remove cross-batch duplicates first
   (different batches often surface the same lesson). Then classify each item by its
   `similar_to` field against the existing `## Conventions` / `## Gotchas` bullets:
   - `similar_to` empty → action `add`
   - `similar_to` set → action `duplicate` (do not auto-merge; surface it to the user — they
     may want to keep both, rewrite, or drop)
   - restates and also corrects an existing bullet → action `modify`
5. **Propose.** Present a checklist (in the user's conversation language) — each row:
   action (`add`/`duplicate`/`modify`), target section, `scope` (`general`/`project`),
   one-line content, source, and `similar_to` (the existing bullet it echoes, when set).
   Sort or group by `scope` so general rules are easy to spot. Rows with `similar_to` set
   default to `duplicate` and are pre-flagged for the user to keep / merge / drop. Let the
   user select.
6. **Apply** only the accepted rows into `## Conventions` / `## Gotchas`.
   **Translate at write time first.** The preview was in the user's conversation language,
   but the saved doc uses its own language (see "Output language"). Translate each accepted
   item into the doc's language before writing — this is an explicit step so the translation
   is never forgotten or done inconsistently.
   **Then locate the target sections.** They may use a different heading level or be in the
   doc's own language (e.g. `### Gotchas`, `## 陷阱`, `## 编码规范`), or may not exist at all
   if the doc predates this skill. Rules:
   - If a section exists (any heading level or language equivalent), append accepted rows
     inside it, preserving its existing heading and list style.
   - If it does not exist, create it. Match the doc's dominant heading level and language;
     place new sections near the end of the doc (just before the marker comment if one is
     present, otherwise at the very end), and tell the user you added them.
   - Leave all other content untouched — only the two skill-owned sections (pre-existing or
     newly added) are edited. Silently rewriting the user's prose breaks trust in the tool.
7. **Advance the marker** (only after writing):
   `python scripts/agents_md.py state advance --file agents.md --commit <head> --mr <last_mr_updated_at>`
   (omit `--mr` if MR was skipped). Because advance runs only after the user accepts, a
   rejected proposal leaves the marker untouched so the same commits/MRs are seen next time.

## Output language

Two different languages — keep them distinct:

- **Conversation & proposal-preview language**: reply and present proposed changes in the
  language the user is using in this conversation (e.g. write the preview in Chinese when
  the user speaks Chinese). Whatever the user reads before approving — the proposal
  checklist, the diff, your explanations — follows their conversation language.
- **Written/save language** (what actually goes into `agents.md`): match the existing doc's
  dominant language, so the file stays internally consistent rather than mixing languages.
  On cold start (no existing doc) use the `language` config, default `en`.

When the two differ, translate the accepted items into the doc's language at write time:
the preview the user approved was in their language, the saved content is in the doc's
language.

## Constraints

These exist for good reasons, not as bureaucracy:

- Only edit within `## Conventions` and `## Gotchas` — the user owns every other section,
  and silently rewriting their prose breaks trust in the tool. If these two sections don't
  exist yet (the doc predates this skill), creating them is the only structural change
  allowed.
- Never advance the marker before the user accepts changes — advancing early would silently
  skip commits/MRs that were never actually written down, defeating the point of incremental
  capture.
- If the marker comment is missing from an existing `agents.md`, the script falls back to
  "last commit touching agents.md" — warn the user when this happens, since the fallback may
  over- or under-count the window.
