# agents.md Auto-Update Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a skill (SKILL.md + `scripts/agents_md.py`) that learns from git `fix` commits and GitLab MR comments and proposes updates to a project's `agents.md` (Gotchas + Conventions sections), with an embedded marker for incremental updates.

**Architecture:** Two layers. `scripts/agents_md.py` is a stdlib-only CLI doing deterministic work (marker parse/render, git commit + diff gathering, GitLab MR fetch via urllib, cache writing, bootstrap gathering). `SKILL.md` orchestrates: it calls the script, dispatches analysis subagents (parallel when data is large) to extract gotchas/conventions from the cache, dedups against existing `agents.md`, proposes changes, and writes only after user confirmation — advancing the embedded marker in the same step.

**Tech Stack:** Python 3 stdlib only (`argparse`, `json`, `re`, `subprocess`, `urllib`, `pathlib`, `datetime`); `pytest` for tests; Markdown for SKILL.md.

**Spec:** `docs/superpowers/specs/2026-06-15-agents-md-auto-update-skill-design.md`

---

## File Structure

- **Create `scripts/agents_md.py`** — single stdlib CLI module. Responsibilities: marker parse/render/read, git gather (standard+deep), GitLab URL inference + MR gather (urllib), advance marker, bootstrap gather, argparse `main()`. Single file for skill portability (copy one file to install). Functions are individually testable; tests import via `tests/conftest.py` putting `scripts/` on `sys.path`.
- **Create `tests/conftest.py`** — puts `scripts/` on `sys.path`; provides repo fixture.
- **Create `tests/test_agents_md.py`** — pytest tests; real temp git repos for git logic, mocked urllib for GitLab.
- **Create `pyproject.toml`** — pytest config + project metadata (no runtime deps).
- **Create `.gitignore`** — ignore `.agents-md-cache/`, `__pycache__/`, `.pytest_cache/`.
- **Create `SKILL.md`** — orchestration instructions (authored last, after the script is proven).

**Public function contract** (defined across tasks — keep names exact):
- `parse_marker(text) -> dict | None`
- `render_marker(state: dict) -> str`
- `read_marker_file(path) -> dict | None`
- `last_commit_touching(path, repo) -> str | None`
- `resolve_since(path, repo) -> str | None`
- `gather_git_commits(since, pattern, mode, repo) -> tuple[list[dict], str]`
- `infer_gitlab(remote_url) -> tuple[str, str] | None`
- `gather_mr_comments(since, base_url, project, token) -> tuple[list[dict], str]`
- `advance_marker(path, commit, mr_ts) -> None`
- `bootstrap_gather(repo, limit) -> dict`
- `write_json(path, data) -> None`
- `now_iso() -> str`
- `main(argv=None) -> int`

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `tests/conftest.py`
- Create: `tests/test_agents_md.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "async-agents-md-skill"
version = "0.1.0"
description = "Skill that learns from git fix commits and MR comments to update agents.md"
requires-python = ">=3.8"

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
.agents-md-cache/
__pycache__/
.pytest_cache/
*.pyc
```

- [ ] **Step 3: Write `tests/conftest.py`**

```python
import sys
from pathlib import Path

# Make scripts/agents_md.py importable as `import agents_md`.
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import subprocess
import pytest


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    """A tiny git repo factory. Returns a callable that commits files."""
    (repo_dir := tmp_path / "repo").mkdir()
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "t@example.com")
    _git(repo_dir, "config", "user.name", "Tester")

    def commit(message, files=None):
        for name, content in (files or {}).items():
            p = repo_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            _git(repo_dir, "add", "--", name)
        _git(repo_dir, "commit", "-q", "-m", message)
        return _git(repo_dir, "rev-parse", "HEAD").strip()

    commit.dir = repo_dir
    return commit
```

- [ ] **Step 4: Write an empty-ish `tests/test_agents_md.py` (smoke import)**

```python
import agents_md


def test_module_imports():
    assert hasattr(agents_md, "main")
```

- [ ] **Step 5: Create a minimal `scripts/agents_md.py` so the import resolves**

```python
"""Deterministic data + state layer for the agents.md updater skill."""


def main(argv=None):
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Install pytest and run the smoke test**

Run: `pip install pytest -q && pytest -q`
Expected: 1 passed (verifies import wiring).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore tests/ scripts/agents_md.py
git commit -m "chore: scaffold pytest project and importable agents_md module"
```

---

## Task 2: Marker parse / render / read

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agents_md.py`:

```python
import json
from pathlib import Path

from agents_md import parse_marker, render_marker, read_marker_file


def test_parse_marker_reads_state():
    text = '# Agents\n\nblah\n\n<!-- agents-md-state: {"schema":1,"last_commit":"abc","last_mr_updated_at":null,"updated_at":"2026-06-15T00:00:00Z"} -->\n'
    state = parse_marker(text)
    assert state == {"schema": 1, "last_commit": "abc",
                     "last_mr_updated_at": None,
                     "updated_at": "2026-06-15T00:00:00Z"}


def test_parse_marker_missing_returns_none():
    assert parse_marker("# no marker here\n") is None
    assert parse_marker("") is None
    assert parse_marker(None) is None


def test_parse_marker_bad_json_returns_none():
    assert parse_marker("<!-- agents-md-state: {not json} -->") is None


def test_render_marker_roundtrips():
    state = {"schema": 1, "last_commit": "deadbeef",
             "last_mr_updated_at": None, "updated_at": "2026-06-15T01:00:00Z"}
    rendered = render_marker(state)
    assert rendered.startswith("<!-- agents-md-state:")
    assert rendered.endswith("-->")
    assert parse_marker(rendered) == state


def test_read_marker_file(tmp_path):
    f = tmp_path / "agents.md"
    f.write_text("# T\n\n<!-- agents-md-state: %s -->\n" %
                 json.dumps({"schema": 1, "last_commit": "xyz",
                             "last_mr_updated_at": None, "updated_at": "u"}))
    assert read_marker_file(f)["last_commit"] == "xyz"


def test_read_marker_file_missing(tmp_path):
    assert read_marker_file(tmp_path / "nope.md") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL (functions undefined).

- [ ] **Step 3: Implement the marker functions**

Add to `scripts/agents_md.py` (after the docstring, before `main`):

```python
import datetime
import json
import re

MARKER_RE = re.compile(r"<!--\s*agents-md-state:\s*(\{.*?\})\s*-->", re.DOTALL)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_marker(text):
    if not text:
        return None
    m = MARKER_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def render_marker(state):
    return "<!-- agents-md-state: " + json.dumps(state, separators=(",", ":")) + " -->"


def read_marker_file(path):
    p = Path(path)
    if not p.exists():
        return None
    return parse_marker(p.read_text(encoding="utf-8"))
```

Also add `from pathlib import Path` to the imports at the top of the file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: marker parse/render/read with embedded HTML comment"
```

---

## Task 3: `last_commit_touching` and `resolve_since` fallback

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import last_commit_touching, resolve_since


def test_last_commit_touching(repo):
    sha1 = repo("feat: init", {"agents.md": "# A\n"})
    sha2 = repo("fix: something", {"src/a.py": "x = 1\n"})
    assert last_commit_touching(repo.dir / "agents.md", repo.dir) == sha1


def test_resolve_since_uses_marker(repo):
    sha1 = repo("feat: init", {"agents.md": "# A\n"})
    repo("fix: later", {"src/b.py": "y = 2\n"})
    state = {"schema": 1, "last_commit": sha1, "last_mr_updated_at": None, "updated_at": "u"}
    (repo.dir / "agents.md").write_text("# A\n\n%s\n" % render_marker(state))
    assert resolve_since(repo.dir / "agents.md", repo.dir) == sha1


def test_resolve_since_falls_back_to_last_touching(repo):
    sha1 = repo("feat: init", {"agents.md": "# A\n"})
    sha2 = repo("fix: later", {"agents.md": "# A v2\n"})  # edits agents.md, no marker
    assert resolve_since(repo.dir / "agents.md", repo.dir) == sha2


def test_resolve_since_none_when_no_file(repo):
    assert resolve_since(repo.dir / "missing.md", repo.dir) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL (functions undefined).

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py`:

```python
import subprocess


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


def last_commit_touching(path, repo):
    try:
        out = _git(repo, "log", "-1", "--format=%H", "--", str(path))
    except subprocess.CalledProcessError:
        return None
    out = out.strip()
    return out or None


def resolve_since(path, repo):
    state = read_marker_file(path) if path and Path(path).exists() else None
    if state and state.get("last_commit"):
        return state["last_commit"]
    if path and Path(path).exists():
        return last_commit_touching(path, repo)
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: resolve incremental 'since' from marker or last touching commit"
```

---

## Task 4: `gather_git_commits` (standard + deep)

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import gather_git_commits


def test_gather_filters_fix_prefix_and_respects_since(repo):
    sha1 = repo("feat: init", {"a.py": "1\n"})
    sha2 = repo("fix: null crash", {"a.py": "2\n", "b.py": "1\n"})
    sha3 = repo("refactor: tidy", {"a.py": "3\n"})
    sha4 = repo("fix(auth): login loop", {"a.py": "4\n"})
    commits, head = gather_git_commits(since=sha1, pattern="^fix", mode="standard", repo=repo.dir)
    shas = [c["sha"] for c in commits]
    assert shas == [sha4, sha2]  # newest first
    assert head == sha3
    entry = commits[0]
    assert entry["message"] == "fix(auth): login loop"
    assert "body" in entry and "diff" not in entry


def test_gather_deep_includes_diff(repo):
    sha1 = repo("feat: init", {"a.py": "1\n"})
    sha2 = repo("fix: add guard", {"a.py": "1\n2\n"})
    commits, _ = gather_git_commits(sha1, "^fix", "deep", repo.dir)
    assert len(commits) == 1
    assert "diff" in commits[0]
    assert "a.py" in commits[0]["diff"]


def test_gather_no_since_returns_all_matching(repo):
    repo("feat: init", {"a.py": "1\n"})
    repo("fix: one", {"a.py": "2\n"})
    commits, _ = gather_git_commits(None, "^fix", "standard", repo.dir)
    assert len(commits) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py`:

```python
def _git_show(repo, sha, fmt):
    return _git(repo, "show", "--no-patch", f"--format={fmt}", sha)


def gather_git_commits(since, pattern, mode, repo):
    rng = f"{since}..HEAD" if since else "HEAD"
    out = _git(repo, "log", "--no-patch", f"--grep={pattern}", "-i", "-E",
               "--format=%H", rng)
    shas = [s for s in out.splitlines() if s.strip()]
    commits = []
    for sha in shas:
        entry = {
            "sha": sha,
            "message": _git_show(repo, sha, "%s").strip(),
            "body": _git_show(repo, sha, "%b").strip(),
        }
        if mode == "deep":
            entry["diff"] = _git(repo, "show", "--format=", "--patch", sha)
        commits.append(entry)
    head = _git(repo, "rev-parse", "HEAD").strip()
    return commits, head
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: gather git fix commits (standard + deep diff)"
```

---

## Task 5: `write_json` + `git gather` subcommand

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import write_json, main


def test_write_json_creates_parents(tmp_path):
    out = tmp_path / ".agents-md-cache" / "commits.json"
    write_json(out, [{"sha": "x"}])
    import json as _j
    assert _j.loads(out.read_text()) == [{"sha": "x"}]


def test_git_gather_cli_writes_cache_and_prints_head(repo, tmp_path, capsys, monkeypatch):
    sha1 = repo("feat: init", {"agents.md": "# A\n"})
    repo("fix: boom", {"a.py": "1\n"})
    cache = tmp_path / "commits.json"
    rc = main(["git", "gather", "--file", str(repo.dir / "agents.md"),
               "--repo", str(repo.dir), "--pattern", "^fix",
               "--mode", "standard", "--out", str(cache)])
    out = capsys.readouterr().out
    assert rc == 0
    import json as _j
    data = _j.loads(cache.read_text())
    assert len(data["commits"]) == 1
    assert data["commits"][0]["message"] == "fix: boom"
    # candidate new head sha printed on stdout as JSON line
    assert _j.loads(out)["head"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement `write_json` and the `git gather` dispatch**

Add to `scripts/agents_md.py`:

```python
def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
```

Replace the existing `main` with a full argparse-based dispatcher (this also seeds subcommands used by later tasks):

```python
import argparse
import sys


def _cmd_git_gather(args):
    since = resolve_since(args.file, args.repo)
    commits, head = gather_git_commits(since, args.pattern, args.mode, args.repo)
    payload = {"since": since, "commits": commits}
    write_json(args.out, payload)
    print(json.dumps({"head": head, "count": len(commits), "cache": args.out}))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="agents_md", description="agents.md updater data/state layer")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("git", help="git data source")
    sp_sub = sp.add_subparsers(dest="git_cmd", required=True)
    gg = sp_sub.add_parser("gather")
    gg.add_argument("--pattern", default="^fix")
    gg.add_argument("--mode", choices=["standard", "deep"], default="standard")
    gg.add_argument("--file", default="agents.md")
    gg.add_argument("--repo", default=".")
    gg.add_argument("--out", default=".agents-md-cache/commits.json")
    gg.set_defaults(func=_cmd_git_gather)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: git gather subcommand writes cache and prints candidate head"
```

---

## Task 6: GitLab URL inference + MR gather (mocked HTTP)

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import infer_gitlab, gather_mr_comments


def test_infer_ssh_remote():
    assert infer_gitlab("git@gitlab.example.com:group/sub.git") == \
        ("https://gitlab.example.com", "group/sub")


def test_infer_https_remote():
    assert infer_gitlab("https://gitlab.example.com/group/sub.git") == \
        ("https://gitlab.example.com", "group/sub")


def test_infer_does_not_restrict_host():
    # Any host is parsed; we intentionally do not filter to *.gitlab.* (the API call
    # itself will simply fail for non-GitLab hosts, which mr gather handles).
    assert infer_gitlab("https://github.com/a/b.git") == ("https://github.com", "a/b")


def test_infer_unparseable_returns_none():
    assert infer_gitlab("not a url") is None


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def read(self):
        import json as _j
        return _j.dumps(self._payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_gather_mr_comments_paginates_and_collects(monkeypatch):
    pages = [
        [{"iid": 1, "title": "MR1", "updated_at": "2026-06-01T00:00:00Z"},
         {"iid": 2, "title": "MR2", "updated_at": "2026-06-10T00:00:00Z"}],
        [],
    ]
    notes = {
        "1": [{"body": "please guard null", "system": False},
              {"body": "marked stale", "system": True}],
        "2": [{"body": "use lowercase", "system": False}],
    }
    calls = []

    def fake_urlopen(req, *a, **k):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        if "/merge_requests?" in url and "/notes" not in url:
            return _FakeResp(pages.pop(0))
        # notes request: extract iid
        import re as _re
        m = _re.search(r"/merge_requests/(\d+)/notes", url)
        return _FakeResp(notes[m.group(1)])

    monkeypatch.setattr("agents_md.urllib.request.urlopen", fake_urlopen)
    mrs, newest = gather_mr_comments(
        since="2026-05-01T00:00:00Z",
        base_url="https://gitlab.example.com",
        project="group/sub", token="tok")
    titles = [m["title"] for m in mrs]
    assert titles == ["MR1", "MR2"]
    assert mrs[0]["comments"] == ["please guard null"]  # system note filtered
    assert mrs[1]["comments"] == ["use lowercase"]
    assert newest == "2026-06-10T00:00:00Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py` (add `import urllib.parse` and `import urllib.request` to imports):

```python
def infer_gitlab(remote_url):
    remote_url = remote_url.strip()
    host = path = None
    if remote_url.startswith("git@"):
        rest = remote_url.split(":", 1)[-1]
        host = remote_url[4:].split(":", 1)[0]
        path = rest
    elif remote_url.startswith("https://") or remote_url.startswith("http://"):
        without = remote_url.split("://", 1)[1]
        host, _, path = without.partition("/")
        host = (remote_url.split("://", 1)[0] + "://" + host)
    if not path:
        return None
    path = path[:-4] if path.endswith(".git") else path
    base = "https://" + host if not host.startswith("http") else host
    return (base, path)


def _http_get_json(url, token):
    req = urllib.request.Request(url)
    if token:
        req.add_header("PRIVATE-TOKEN", token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def gather_mr_comments(since, base_url, project, token, per_page=100, max_pages=20):
    project_enc = urllib.parse.quote(project, safe="")
    base = f"{base_url}/api/v4/projects/{project_enc}/merge_requests"
    mrs = []
    page = 1
    while page <= max_pages:
        qs = [f"state=merged", f"per_page={per_page}", f"page={page}",
              "order_by=updated_at", "sort=asc"]
        if since:
            qs.append(f"updated_after={since}")
        data = _http_get_json(base + "?" + "&".join(qs), token)
        if not data:
            break
        mrs.extend(data)
        if len(data) < per_page:
            break
        page += 1

    result = []
    newest = since or ""
    for mr in mrs:
        iid = mr["iid"]
        notes = _http_get_json(
            f"{base}/{iid}/notes?per_page={per_page}", token)
        comments = [n["body"] for n in notes if not n.get("system")]
        result.append({"iid": iid, "title": mr.get("title", ""),
                       "updated_at": mr.get("updated_at", ""), "comments": comments})
        if mr.get("updated_at", "") > newest:
            newest = mr["updated_at"]
    return result, newest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: infer GitLab URL and gather MR comments via REST API"
```

---

## Task 7: `mr gather` subcommand (token + remote inference)

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
def test_mr_gather_cli_uses_remote_and_token(repo, tmp_path, monkeypatch, capsys):
    repo("feat: init", {"README.md": "# x\n"})
    # set origin remote
    import subprocess as _sp
    _sp.run(["git", "-C", str(repo.dir), "remote", "add", "origin",
             "git@gitlab.example.com:group/sub.git"], check=True)
    captured = {}

    def fake_gather(since, base_url, project, token):
        captured.update(since=since, base_url=base_url, project=project, token=token)
        return ([{"iid": 9, "title": "T", "updated_at": "2026-06-12T00:00:00Z",
                  "comments": ["c"]}], "2026-06-12T00:00:00Z")

    monkeypatch.setattr("agents_md.gather_mr_comments", fake_gather)
    monkeypatch.setenv("GITLAB_TOKEN", "secret")
    cache = tmp_path / "mrs.json"
    rc = main(["mr", "gather", "--via", "api", "--repo", str(repo.dir),
               "--since", "2026-06-01T00:00:00Z", "--out", str(cache)])
    assert rc == 0
    assert captured["base_url"] == "https://gitlab.example.com"
    assert captured["project"] == "group/sub"
    assert captured["token"] == "secret"
    import json as _j
    assert _j.loads(cache.read_text())["mrs"][0]["iid"] == 9
    out = _j.loads(capsys.readouterr().out)
    assert out["last_mr_updated_at"] == "2026-06-12T00:00:00Z"


def test_mr_gather_cli_no_token_exits_nonzero(repo, monkeypatch):
    repo("feat: init", {"README.md": "# x\n"})
    import subprocess as _sp
    _sp.run(["git", "-C", str(repo.dir), "remote", "add", "origin",
             "git@gitlab.example.com:group/sub.git"], check=True)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    rc = main(["mr", "gather", "--via", "api", "--repo", str(repo.dir)])
    assert rc != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py`:

```python
def _remote_origin(repo):
    try:
        return _git(repo, "config", "--get", "remote.origin.url").strip()
    except subprocess.CalledProcessError:
        return ""


def _cmd_mr_gather(args):
    token = os.environ.get(args.gitlab_token_env)
    if not token:
        print(json.dumps({"error": f"env {args.gitlab_token_env} not set"}))
        return 2
    remote = _remote_origin(args.repo)
    inferred = infer_gitlab(remote)
    if not inferred:
        print(json.dumps({"error": f"could not infer GitLab from remote: {remote!r}"}))
        return 2
    base_url, project = inferred
    mrs, newest = gather_mr_comments(args.since, base_url, project, token)
    write_json(args.out, {"mrs": mrs})
    print(json.dumps({"last_mr_updated_at": newest, "count": len(mrs), "cache": args.out}))
    return 0
```

Add `import os` to the imports. Register the subcommand inside `build_parser`, after the `git` block:

```python
    mr = sub.add_parser("mr", help="GitLab MR data source")
    mr_sub = mr.add_subparsers(dest="mr_cmd", required=True)
    mrg = mr_sub.add_parser("gather")
    mrg.add_argument("--via", choices=["api"], default="api")
    mrg.add_argument("--since", default=None)
    mrg.add_argument("--repo", default=".")
    mrg.add_argument("--out", default=".agents-md-cache/mrs.json")
    mrg.add_argument("--gitlab-token-env", default="GITLAB_TOKEN", dest="gitlab_token_env")
    mrg.set_defaults(func=_cmd_mr_gather)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: mr gather subcommand (token + remote inference, graceful errors)"
```

---

## Task 8: `advance_marker` + `state advance` / `state show` subcommands

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import advance_marker


def test_advance_inserts_when_absent(tmp_path):
    f = tmp_path / "agents.md"
    f.write_text("# A\n\nsome content\n")
    advance_marker(f, commit="aaa", mr_ts=None)
    state = parse_marker(f.read_text())
    assert state["last_commit"] == "aaa"
    assert state["last_mr_updated_at"] is None


def test_advance_updates_existing(tmp_path):
    f = tmp_path / "agents.md"
    old = render_marker({"schema": 1, "last_commit": "old", "last_mr_updated_at": None, "updated_at": "u"})
    f.write_text(f"# A\n\n{old}\n")
    advance_marker(f, commit="new", mr_ts="2026-06-12T00:00:00Z")
    state = parse_marker(f.read_text())
    assert state["last_commit"] == "new"
    assert state["last_mr_updated_at"] == "2026-06-12T00:00:00Z"
    # only one marker remains
    assert f.read_text().count("agents-md-state:") == 1


def test_state_show_cli(tmp_path, capsys):
    f = tmp_path / "agents.md"
    f.write_text("# A\n\n%s\n" % render_marker(
        {"schema": 1, "last_commit": "z", "last_mr_updated_at": None, "updated_at": "u"}))
    rc = main(["state", "show", "--file", str(f)])
    assert rc == 0
    import json as _j
    assert _j.loads(capsys.readouterr().out)["last_commit"] == "z"


def test_state_advance_cli(tmp_path):
    f = tmp_path / "agents.md"
    f.write_text("# A\n")
    rc = main(["state", "advance", "--file", str(f), "--commit", "abc"])
    assert rc == 0
    assert parse_marker(f.read_text())["last_commit"] == "abc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py`:

```python
def advance_marker(path, commit, mr_ts):
    p = Path(path)
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    state = parse_marker(text) or {}
    state["schema"] = 1
    state["last_commit"] = commit
    if mr_ts is not None:
        state["last_mr_updated_at"] = mr_ts
    state.setdefault("last_mr_updated_at", None)
    state["updated_at"] = now_iso()
    new_marker = render_marker(state)
    if MARKER_RE.search(text):
        text = MARKER_RE.sub(lambda _m: new_marker, text, count=1)
    else:
        text = text.rstrip() + "\n\n" + new_marker + "\n"
    p.write_text(text, encoding="utf-8")


def _cmd_state_show(args):
    state = read_marker_file(args.file)
    if state is None:
        fallback = last_commit_touching(args.file, ".") if Path(args.file).exists() else None
        print(json.dumps({"marker": None, "fallback_since": fallback,
                          "note": "no marker; using last commit touching file"}))
    else:
        print(json.dumps(state))
    return 0


def _cmd_state_advance(args):
    advance_marker(args.file, args.commit, args.mr)
    return 0
```

Register inside `build_parser` (alongside the other subcommands):

```python
    st = sub.add_parser("state", help="marker state")
    st_sub = st.add_subparsers(dest="state_cmd", required=True)
    ss = st_sub.add_parser("show")
    ss.add_argument("--file", default="agents.md")
    ss.set_defaults(func=_cmd_state_show)
    sa = st_sub.add_parser("advance")
    sa.add_argument("--file", default="agents.md")
    sa.add_argument("--commit", required=True)
    sa.add_argument("--mr", default=None)
    sa.set_defaults(func=_cmd_state_advance)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: state show/advance subcommands and marker insertion/update"
```

---

## Task 9: `bootstrap_gather` + `bootstrap gather` subcommand

**Files:**
- Modify: `scripts/agents_md.py`
- Test: `tests/test_agents_md.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
from agents_md import bootstrap_gather


def test_bootstrap_gather_collects_readme_tree_commits(repo):
    repo("feat: init", {"README.md": "# Project\n", "src/a.py": "1\n", "docs/b.md": "2\n"})
    repo("fix: one", {"src/a.py": "2\n"})
    data = bootstrap_gather(repo.dir, limit=10)
    assert "Project" in data["readme"]
    assert "src" in data["top_level"] and "docs" in data["top_level"]
    assert any("fix: one" in line for line in data["recent_commits"])


def test_bootstrap_cli(repo, tmp_path, capsys):
    repo("feat: init", {"README.md": "# P\n"})
    out = tmp_path / "boot.json"
    rc = main(["bootstrap", "gather", "--repo", str(repo.dir), "--out", str(out)])
    assert rc == 0
    import json as _j
    assert _j.loads(out.read_text())["readme"].startswith("# P")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_agents_md.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `scripts/agents_md.py`:

```python
def _find_readme(repo):
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = Path(repo) / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def bootstrap_gather(repo, limit=50):
    readme = _find_readme(repo)
    files = _git(repo, "ls-files").splitlines()
    top = sorted({f.split("/")[0] for f in files if f} - {""})
    log = _git(repo, "log", "--no-patch", f"-{limit}", "--format=%H %s").strip()
    recent = log.splitlines() if log else []
    return {"readme": readme, "top_level": top, "recent_commits": recent}


def _cmd_bootstrap_gather(args):
    data = bootstrap_gather(args.repo, args.limit)
    write_json(args.out, data)
    print(json.dumps({"cache": args.out,
                      "top_level_count": len(data["top_level"]),
                      "recent_count": len(data["recent_commits"])}))
    return 0
```

Register inside `build_parser`:

```python
    bs = sub.add_parser("bootstrap", help="cold-start data gathering")
    bs_sub = bs.add_subparsers(dest="bootstrap_cmd", required=True)
    bsg = bs_sub.add_parser("gather")
    bsg.add_argument("--repo", default=".")
    bsg.add_argument("--limit", type=int, default=50)
    bsg.add_argument("--out", default=".agents-md-cache/bootstrap.json")
    bsg.set_defaults(func=_cmd_bootstrap_gather)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_agents_md.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/agents_md.py tests/test_agents_md.py
git commit -m "feat: bootstrap gather (readme + tree + recent commits)"
```

---

## Task 10: Full test pass + CLI help sanity

**Files:**
- Test: whole suite

- [ ] **Step 1: Run the full suite**

Run: `pytest -q`
Expected: all tests pass (count up to this point).

- [ ] **Step 2: Verify CLI help renders**

Run: `python scripts/agents_md.py --help && python scripts/agents_md.py git gather --help`
Expected: usage text prints without error.

- [ ] **Step 3: Commit (if any cleanup)**

If you tweaked anything, commit it:
```bash
git add -A && git commit -m "chore: full test pass" || echo "nothing to commit"
```

---

## Task 11: Author `SKILL.md`

**Files:**
- Create: `SKILL.md`

- [ ] **Step 1: Write `SKILL.md`**

```markdown
---
name: async-agents-md
description: Learn from git fix commits and GitLab MR comments, then propose updates to the project's agents.md (Gotchas + Conventions). Use when the user wants to update agents.md, refresh project context for AI agents, or capture lessons learned from recent fixes and merge request reviews.
---

# agents.md updater

You maintain the project's `agents.md` (community-standard AI context doc). You extract
**gotchas** (mostly from `fix` commits) and **coding conventions** (mostly from MR review
comments), dedup them against the existing doc, and propose changes for user approval.
Only the `## Conventions` and `## Gotchas` sections are ever auto-edited; everything else
is user-owned. An HTML-comment marker at the end of `agents.md` records incremental
progress and advances ONLY after the user accepts proposed changes.

## Setup
The data/state layer is `scripts/agents_md.py` (stdlib only). Run it via the repo root,
e.g. `python scripts/agents_md.py <subcommand>`. Cache files are written under
`.agents-md-cache/` (gitignored).

## Configuration (from $ARGUMENTS or ask the user)
- `mode`: `standard` (default) | `deep` (also reads commit diffs)
- `pattern`: regex for fix commits, default `^fix`
- `language`: first-run language, default `en`; afterward follow the existing doc's language
- `target`: file name, default `agents.md`
- `batch_size`: items per analysis batch, default `10`
- `analysis_mode`: `parallel` (default) | `sequential`

Parse `$ARGUMENTS` as `key=value` tokens. Defaults shown above.

## Procedure

### 1. Decide cold-start vs incremental
- If `target` does NOT exist → go to **Bootstrap**.
- If it exists → go to **Incremental**.

### 2. Bootstrap (cold start)
1. Run: `python scripts/agents_md.py bootstrap gather --repo . --out .agents-md-cache/bootstrap.json`
2. Read `.agents-md-cache/bootstrap.json` (README + top-level dirs + recent commits).
3. Compose a full `agents.md` in the configured language using this structure:
   - `# Agents` + one-line purpose note
   - `## Overview`
   - `## Build & Test` (infer from README / common files; leave a TODO only if truly absent)
   - `## Code Layout`
   - `## Conventions` (start empty or with obvious items)
   - `## Gotchas` (start empty)
4. Write the file WITHOUT the marker, then run:
   `python scripts/agents_md.py state advance --file agents.md --commit $(git rev-parse HEAD)`
5. Show the user the generated file. Done (no proposal step on cold start).

### 3. Incremental
1. Gather git data:
   `python scripts/agents_md.py git gather --file agents.md --pattern <pattern> --mode <mode> --out .agents-md-cache/commits.json`
   Note the `head` SHA printed on stdout.
2. Gather MR data. First try the **GitLab MCP** if available (list MRs updated after the
   marker's `last_mr_updated_at`, fetch their non-system comments). If the MCP is not
   available, fall back to the script:
   `python scripts/agents_md.py mr gather --via api --repo . --out .agents-md-cache/mrs.json`
   Note the `last_mr_updated_at` printed on stdout. If neither is available, skip MR
   (only git data is used) and tell the user.
3. **Analyze** the cache. Dispatch analysis subagents (one per `batch_size` chunk of items;
   parallel by default per `dispatching-parallel-agents`, or sequential if
   `analysis_mode=sequential`). Each subagent reads its chunk from
   `.agents-md-cache/commits.json` and `.agents-md-cache/mrs.json` and returns JSON:
   `{"gotchas": ["..."], "conventions": ["..."]}` with each item tagged by source
   (commit sha / MR iid). If total items <= `batch_size`, a single subagent suffices.
4. **Merge + dedup**: combine all subagents' outputs; remove cross-batch duplicates; then
   compare each candidate against existing `## Conventions` and `## Gotchas` bullets:
   - novel → action `add`
   - near-duplicate of an existing bullet → action `duplicate` (do NOT auto-merge)
   - refines an existing bullet → action `modify`
5. **Propose**: present a checklist to the user — each row: action, target section,
   one-line content, source, similarity-to-existing. Let the user select rows.
6. **Apply** only the accepted rows: edit the `## Conventions` / `## Gotchas` blocks in
   `agents.md` (respect the HTML-comment block boundaries; never touch other sections).
7. **Advance the marker** (only after writing):
   `python scripts/agents_md.py state advance --file agents.md --commit <head> --mr <last_mr_updated_at>`
   (omit `--mr` if MR was skipped). Because advance runs only after the user accepts, a
   rejected proposal leaves the marker untouched so the same commits/MRs are seen next time.

## Output language
First run uses the configured `language`. On subsequent runs, detect the dominant language
of the existing `agents.md` and match it.

## Constraints
- Never edit sections other than `## Conventions` and `## Gotchas`.
- Never advance the marker before the user accepts changes.
- If the marker comment is missing from an existing `agents.md`, the script falls back to
  "last commit touching agents.md"; warn the user.
```

- [ ] **Step 2: Validate the SKILL.md references resolve**

Run: `python scripts/agents_md.py --help` and confirm the subcommands named in SKILL.md (`bootstrap gather`, `git gather`, `mr gather`, `state show`, `state advance`) all exist.
Expected: all five subcommands present in help output.

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "feat: author SKILL.md orchestration (bootstrap + incremental + parallel analysis)"
```

---

## Task 12: End-to-end validation in this repo

**Files:**
- Run-only validation (no new files committed unless fixing issues)

- [ ] **Step 1: Cold-start bootstrap in this repo**

Run:
```bash
python scripts/agents_md.py bootstrap gather --repo . --out .agents-md-cache/bootstrap.json
cat .agents-md-cache/bootstrap.json
```
Expected: JSON with the repo's README content, top-level entries (`scripts`, `tests`, `docs`, ...), and recent commits.

- [ ] **Step 2: Simulate a fix commit and incremental gather**

```bash
echo "x = 1" > /tmp/probe.py 2>/dev/null
git commit --allow-empty -q -m "fix: simulate a bugfix for e2e check"
python scripts/agents_md.py git gather --file agents.md --pattern '^fix' --mode standard --out .agents-md-cache/commits.json
cat .agents-md-cache/commits.json
git reset -q --hard HEAD~1
```
Expected: `commits.json` contains the simulated fix commit (and the real ones if any). Then the reset removes the empty probe commit.

- [ ] **Step 3: Verify marker advance round-trip on a scratch agents.md**

```bash
printf '# Agents\n\n## Gotchas\n- none yet\n' > /tmp/agents-scratch.md
python scripts/agents_md.py state advance --file /tmp/agents-scratch.md --commit "$(git rev-parse HEAD)"
python scripts/agents_md.py state show --file /tmp/agents-scratch.md
```
Expected: `state show` prints JSON whose `last_commit` equals the current HEAD.

- [ ] **Step 4: Commit any e2e-driven fixes**

```bash
git add -A && git commit -m "fix: e2e validation adjustments" || echo "nothing to commit"
```

---

## Done criteria

- `scripts/agents_md.py` runs all five subcommand groups; `pytest -q` is green.
- `SKILL.md` documents bootstrap + incremental flows and references real subcommands.
- Marker round-trips (parse → advance → show) correctly in this repo.
- `.agents-md-cache/` is gitignored.
