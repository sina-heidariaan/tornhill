#!/usr/bin/env python3
"""
aerial-mine-git — extract architecture-relevant signals from a project's git
history that a static code snapshot cannot reveal.

These feed the aerial findings overlay:
  - churn          : files touched most often (change pressure)
  - co_change      : file pairs that change together, esp. across modules
                     (HIDDEN COUPLING)
  - growth         : files with the largest net line growth (god-class trajectory)
  - fix_hotspots   : files most often touched by fix/hotfix/bug/revert commits
                     (recurring pain)

Concepts (temporal coupling, hotspots) are inspired by Adam Tornhill's
behavioral-code-analysis work (Code Maat / "Your Code as a Crime Scene").

Pure stdlib. Read-only. No network. Operates on ONE git repo.

Usage:
    python aerial-mine-git.py <project-dir> [options]

Options:
    --since <git-date>   default: "12 months ago"
    --max-commits <n>    cap on commits scanned (default 4000)
    --top <n>            rows per section (default 15)
    --module-depth <n>   path components that define a "module" (default 2)
    --cochange-cap <n>   skip commits touching more files than this for the
                         co-change pass (merges/refactors add noise) (default 40)
    --min-support <n>    min shared commits for a co-change pair (default 3)
    --format json|md     default json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

FIX_RE = re.compile(r"\b(fix|fixes|fixed|hotfix|bug|bugfix|revert|regression)\b", re.I)
IGNORE_RE = re.compile(
    r"(^|/)(node_modules|dist|build|\.next|Pods|DerivedData|\.venv|vendor|"
    r"package-lock\.json|yarn\.lock|uv\.lock|Podfile\.lock|\.lock)(/|$)"
)
CODE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".swift", ".go", ".rb", ".java",
    ".kt", ".rs", ".php", ".cs", ".scala", ".m", ".mm", ".c", ".cpp", ".h",
}


def run_git(repo: Path, args: list[str]) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, check=True,
        )
        return out.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"git failed: {' '.join(args)}\n{exc}", file=sys.stderr)
        return ""


def is_code(path: str) -> bool:
    if IGNORE_RE.search(path):
        return False
    return Path(path).suffix in CODE_EXT


def module_of(path: str, depth: int) -> str:
    parts = path.split("/")
    return "/".join(parts[:depth]) if len(parts) > depth else path


def parse_log(repo: Path, since: str, max_commits: int):
    fmt = "%x1e%H%x1f%s"
    raw = run_git(
        repo,
        ["log", f"--since={since}", f"--max-count={max_commits}",
         "--no-merges", "--numstat", f"--format={fmt}"],
    )
    if not raw:
        return
    for block in raw.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        header, _, body = block.partition("\n")
        chash, _, subject = header.partition("\x1f")
        files = []
        for line in body.splitlines():
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            added, removed, path = cols
            if not is_code(path):
                continue
            a = int(added) if added.isdigit() else 0
            r = int(removed) if removed.isdigit() else 0
            files.append((path, a, r))
        if files:
            yield {"hash": chash, "is_fix": bool(FIX_RE.search(subject)), "files": files}


def mine(repo: Path, args) -> dict:
    churn = defaultdict(int)
    growth = defaultdict(int)
    fix_touch = defaultdict(int)
    pair_support = defaultdict(int)
    commits = 0

    for c in parse_log(repo, args.since, args.max_commits):
        commits += 1
        paths = [p for p, _, _ in c["files"]]
        for p, a, r in c["files"]:
            churn[p] += 1
            growth[p] += a - r
            if c["is_fix"]:
                fix_touch[p] += 1
        if 2 <= len(paths) <= args.cochange_cap:
            for a_p, b_p in combinations(sorted(set(paths)), 2):
                pair_support[(a_p, b_p)] += 1

    top, md = args.top, args.module_depth
    churn_top = sorted(churn.items(), key=lambda x: -x[1])[:top]
    growth_top = sorted(growth.items(), key=lambda x: -x[1])[:top]
    fix_top = sorted(fix_touch.items(), key=lambda x: -x[1])[:top]

    cochange = []
    for (a_p, b_p), support in pair_support.items():
        if support < args.min_support:
            continue
        ma, mb = module_of(a_p, md), module_of(b_p, md)
        if ma == mb:
            continue
        conf = support / max(1, min(churn[a_p], churn[b_p]))
        cochange.append({
            "a": a_p, "b": b_p, "module_a": ma, "module_b": mb,
            "shared_commits": support, "confidence": round(conf, 2),
        })
    cochange.sort(key=lambda x: (-x["confidence"], -x["shared_commits"]))
    cochange = cochange[:top]

    return {
        "project": str(repo),
        "since": args.since,
        "commits_scanned": commits,
        "churn": [{"path": p, "touches": n} for p, n in churn_top],
        "co_change": cochange,
        "growth": [{"path": p, "net_lines": n} for p, n in growth_top if n > 0],
        "fix_hotspots": [{"path": p, "fix_touches": n} for p, n in fix_top if n > 0],
    }


def to_md(data: dict) -> str:
    L = [f"# git signals — {data['project']}",
         f"_window: {data['since']} · commits scanned: {data['commits_scanned']}_\n"]
    L.append("## Hidden coupling (cross-module co-change)\n")
    if data["co_change"]:
        L += ["| A | B | shared | confidence |", "|---|---|--------|------------|"]
        for r in data["co_change"]:
            L.append(f"| `{r['a']}` | `{r['b']}` | {r['shared_commits']} | {r['confidence']} |")
    else:
        L.append("_none above thresholds_")
    L.append("\n## Churn (change pressure)\n")
    L += ["| file | touches |", "|------|---------|"]
    for r in data["churn"]:
        L.append(f"| `{r['path']}` | {r['touches']} |")
    L.append("\n## Growth trajectory (god-class watch)\n")
    L += ["| file | net lines |", "|------|-----------|"]
    for r in data["growth"]:
        L.append(f"| `{r['path']}` | +{r['net_lines']} |")
    L.append("\n## Fix hotspots (recurring pain)\n")
    L += ["| file | fix touches |", "|------|-------------|"]
    for r in data["fix_hotspots"]:
        L.append(f"| `{r['path']}` | {r['fix_touches']} |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine git history for architecture signals.")
    ap.add_argument("project", help="path to the target project (a git repo)")
    ap.add_argument("--since", default="12 months ago")
    ap.add_argument("--max-commits", type=int, default=4000)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--module-depth", type=int, default=2)
    ap.add_argument("--cochange-cap", type=int, default=40)
    ap.add_argument("--min-support", type=int, default=3)
    ap.add_argument("--format", choices=["json", "md"], default="json")
    args = ap.parse_args()

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 1
    data = mine(repo, args)
    print(to_md(data) if args.format == "md" else json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
