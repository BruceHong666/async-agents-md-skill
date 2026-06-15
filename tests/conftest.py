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
