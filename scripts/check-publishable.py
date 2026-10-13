"""Check the Git index/HEAD, never private runtime directories, before publishing."""
from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import PurePosixPath


DENIED = {"data", "backups", "history", "runtime", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
PATTERNS = ("*.sqlite*", "*.zip", "*.log", ".env*", "*.pem", "*.pfx", "*.key",
            "config.json", "config.local.json", "oauth-client.json", "client_secret*.json",
            "credentials*.json", "*token*.json")
SECRETS = re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|"
                     r"GOCSPX-[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")


def violations(name, data):
    path = PurePosixPath(name)
    if any(part.lower() in DENIED or part.endswith(".egg-info") for part in path.parts):
        return ["private/generated directory"]
    if any(fnmatch.fnmatch(path.name.lower(), pattern) for pattern in PATTERNS):
        return ["private/generated filename"]
    if SECRETS.search(data.decode("utf-8", errors="replace")):
        return ["credential-shaped content"]
    return []


def main():
    names = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
    failed = []
    for name in filter(None, names):
        # Read the staged blob, so a clean worktree cannot disguise an unsafe index.
        data = subprocess.check_output(["git", "show", ":" + name])
        failed.extend((name, reason) for reason in violations(name, data))
    for name, reason in failed:
        print(f"FAIL: {name}: {reason}")
    if failed:
        return 1
    print(f"PASS: {sum(bool(name) for name in names)} tracked files; no private runtime files or credential-shaped content.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
