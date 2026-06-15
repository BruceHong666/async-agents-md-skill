import agents_md


def test_module_imports():
    assert hasattr(agents_md, "main")


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


from agents_md import gather_git_commits


def test_gather_filters_fix_prefix_and_respects_since(repo):
    sha1 = repo("feat: init", {"a.py": "1\n"})
    sha2 = repo("fix: null crash", {"a.py": "2\n", "b.py": "1\n"})
    sha3 = repo("refactor: tidy", {"a.py": "3\n"})
    sha4 = repo("fix(auth): login loop", {"a.py": "4\n"})
    commits, head = gather_git_commits(since=sha1, pattern="^fix", mode="standard", repo=repo.dir)
    shas = [c["sha"] for c in commits]
    assert shas == [sha4, sha2]  # newest first
    assert head == sha4  # HEAD is always the most recent commit
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


def test_state_show_fallback_uses_repo(repo, capsys):
    # agents.md exists but has no marker -> fallback to last commit touching it,
    # resolved against --repo (not cwd), so it works when run from another dir.
    sha1 = repo("feat: init", {"agents.md": "# A\n"})
    repo("fix: later", {"src/a.py": "1\n"})  # does not touch agents.md
    rc = main(["state", "show", "--file", str(repo.dir / "agents.md"),
               "--repo", str(repo.dir)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["marker"] is None
    assert out["fallback_since"] == sha1


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
