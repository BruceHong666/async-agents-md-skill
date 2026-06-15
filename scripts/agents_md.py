"""Deterministic data + state layer for the agents.md updater skill.

Pure stdlib (argparse, json, re, subprocess, urllib, pathlib, datetime, os).
Single file for skill portability (copy one file to install).
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MARKER_RE = re.compile(r"<!--\s*agents-md-state:\s*(\{.*?\})\s*-->", re.DOTALL)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


# --------------------------------------------------------------------------
# Marker parse / render / read
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Incremental 'since' resolution
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Git commit gather (standard + deep)
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Cache writer
# --------------------------------------------------------------------------

def write_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------
# GitLab URL inference + MR gather
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# advance_marker
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# bootstrap gather
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# CLI command handlers
# --------------------------------------------------------------------------

def _cmd_git_gather(args):
    since = resolve_since(args.file, args.repo)
    commits, head = gather_git_commits(since, args.pattern, args.mode, args.repo)
    payload = {"since": since, "commits": commits}
    write_json(args.out, payload)
    print(json.dumps({"head": head, "count": len(commits), "cache": args.out}))
    return 0


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


def _cmd_bootstrap_gather(args):
    data = bootstrap_gather(args.repo, args.limit)
    write_json(args.out, data)
    print(json.dumps({"cache": args.out,
                      "top_level_count": len(data["top_level"]),
                      "recent_count": len(data["recent_commits"])}))
    return 0


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------

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

    mr = sub.add_parser("mr", help="GitLab MR data source")
    mr_sub = mr.add_subparsers(dest="mr_cmd", required=True)
    mrg = mr_sub.add_parser("gather")
    mrg.add_argument("--via", choices=["api"], default="api")
    mrg.add_argument("--since", default=None)
    mrg.add_argument("--repo", default=".")
    mrg.add_argument("--out", default=".agents-md-cache/mrs.json")
    mrg.add_argument("--gitlab-token-env", default="GITLAB_TOKEN", dest="gitlab_token_env")
    mrg.set_defaults(func=_cmd_mr_gather)

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

    bs = sub.add_parser("bootstrap", help="cold-start data gathering")
    bs_sub = bs.add_subparsers(dest="bootstrap_cmd", required=True)
    bsg = bs_sub.add_parser("gather")
    bsg.add_argument("--repo", default=".")
    bsg.add_argument("--limit", type=int, default=50)
    bsg.add_argument("--out", default=".agents-md-cache/bootstrap.json")
    bsg.set_defaults(func=_cmd_bootstrap_gather)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
