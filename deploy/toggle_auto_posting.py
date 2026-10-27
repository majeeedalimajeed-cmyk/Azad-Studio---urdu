#!/usr/bin/env python3
"""Start or stop AZAD STUDIO GitHub Actions auto posting.

Reads a GitHub token from GITHUB_TOKEN, GH_TOKEN, or ~/.git-credentials.
The token is never printed. Repository variables are non-secret.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import requests

DEFAULT_REPO = "majeeedalimajeed-cmyk/Azad-Studio---urdu"

START_VALUES = {
    "NR_DRY_RUN": "false",
    "NR_PUBLISH_ENABLED": "true",
    "NR_PUBLISH_SAFETY_OK": "true",
    "NR_OPERATOR_APPROVED": "true",
    "NR_VERIFY_APPROVED": "true",
    "NR_AUTO_APPROVE_VERIFIED": "true",
    "NR_AUTO_POST_ENABLED": "true",
}

STOP_VALUES = {
    "NR_DRY_RUN": "true",
    "NR_PUBLISH_ENABLED": "false",
    "NR_PUBLISH_SAFETY_OK": "false",
    "NR_OPERATOR_APPROVED": "false",
    "NR_VERIFY_APPROVED": "false",
    "NR_AUTO_APPROVE_VERIFIED": "false",
    "NR_AUTO_POST_ENABLED": "false",
}

WATCHED = sorted(START_VALUES)


def find_token() -> str:
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    path = Path.home() / ".git-credentials"
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
        match = re.search(r"https://x-access-token:([^@\s]+)@github\.com", text)
        if match:
            return match.group(1)
    raise SystemExit("No GitHub token found. Set GITHUB_TOKEN/GH_TOKEN or ~/.git-credentials.")


def session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return s


def set_variable(s: requests.Session, repo: str, name: str, value: str) -> None:
    base = f"https://api.github.com/repos/{repo}/actions/variables"
    r = s.patch(f"{base}/{name}", json={"value": value}, timeout=30)
    if r.status_code == 404:
        r = s.post(base, json={"name": name, "value": value}, timeout=30)
    if r.status_code >= 400:
        # Do not print response bodies: they can contain sensitive diagnostics.
        raise RuntimeError(f"GitHub API returned HTTP {r.status_code} for {name}")
    print(f"set {name}={value}")


def show_status(s: requests.Session, repo: str) -> None:
    r = s.get(f"https://api.github.com/repos/{repo}/actions/variables?per_page=100", timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"GitHub API returned HTTP {r.status_code}")
    values = {item["name"]: item["value"] for item in r.json().get("variables", [])}
    for name in WATCHED:
        print(f"{name}={values.get(name, '<missing>')}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--start", action="store_true", help="Enable live auto posting")
    group.add_argument("--stop", action="store_true", help="Disable live auto posting")
    group.add_argument("--status", action="store_true", help="Show current auto-post variables")
    p.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo")
    args = p.parse_args()

    s = session(find_token())
    if args.status:
        show_status(s, args.repo)
        return 0
    values = START_VALUES if args.start else STOP_VALUES
    for name, value in values.items():
        set_variable(s, args.repo, name, value)
    print("done")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
