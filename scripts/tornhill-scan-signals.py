#!/usr/bin/env python3
"""
tornhill-scan-signals — a deterministic grep-bank of CANDIDATE risk sinks for the
`/audit` skill (and the `/tornhill deep` analysis lenses).

Every row here is a CANDIDATE, not a verdict. The script cites a real `path:line`
and an excerpt; the LLM then confirms it against the surrounding code (does the
`email` field actually reach a response? is that `req.params.id` lookup really
missing an ownership check?) and DROPS the ones that aren't real. This mirrors the
producer/consumer split of tornhill-mine-git.py -> findings: cheap, grounded
candidates in, reasoned findings out. It never asserts a finding on its own.

Signal families -> finding families:
  pii         -> privacy      (sensitive field near a response/serializer)
  log         -> privacy      (request/response/secret logged)
  injection   -> security     (string-built SQL, eval, shell, SSRF url fetch)
  authz       -> security     (id lookup w/o ownership check, mass assignment)
  secret      -> security     (hardcoded key/token)
  cost        -> cost         (N+1 / unbounded query / missing pagination)
  perf        -> performance  (blocking sync call, nested loops)
  correctness -> correctness  (swallowed error, loose equality, unseeded id)

Pure stdlib. Read-only. No network. One git repo.

Usage:
    python tornhill-scan-signals.py <project-dir> [options]

Options:
    --families <list>   subset of pii,log,injection,authz,secret,cost,perf,correctness
                        (default: all)
    --discovery git|walk   how files are found (default git = `git ls-files`)
    --max-per-family <n>   cap rows per family (default 200, keeps output bounded)
    --format json|md    default json (schema tornhill.signals/v1)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

IGNORE_RE = re.compile(
    r"(^|/)(node_modules|dist|build|\.next|Pods|DerivedData|\.venv|vendor|"
    r"__pycache__|test|tests|__tests__|spec|\.spec\.|\.test\.|"
    r"package-lock\.json|yarn\.lock|uv\.lock)(/|$)"
)
CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".java", ".kt", ".rb", ".go", ".php", ".cs"}

# --- pattern bank: family -> list of (subtype, compiled regex) ---
PII_FIELDS = (r"ssn|social_?security|passport|national_?id|tax_?id|"
              r"credit_?card|card_?number|cardnumber|cvv|cvc|iban|routing_?number|"
              r"date_?of_?birth|dob|birth_?date|"
              r"medical_?record|mrn|diagnosis|health|patient|"
              r"email|phone|mobile|address|full_?name|first_?name|last_?name|"
              r"drivers?_?license|biometric")

PATTERNS = {
    "pii": [
        ("pii_field", re.compile(rf"""['"]?\b({PII_FIELDS})\b['"]?\s*[:=]""", re.IGNORECASE)),
    ],
    "log": [
        ("log_sensitive", re.compile(
            r"""(console\.(log|info|debug|error|warn)|logger?\.\w+|print|println|System\.out|"""
            r"""fmt\.Print\w*)\s*\(.*\b(req|request|res|response|body|headers?|token|"""
            r"""password|passwd|secret|authorization|cookie|session)\b""", re.IGNORECASE)),
    ],
    "injection": [
        ("sql_concat", re.compile(
            r"""(SELECT|INSERT|UPDATE|DELETE)\b.*?(\+|\$\{|%\s|%\(|f['"]|\|\|)""", re.IGNORECASE)),
        ("eval", re.compile(r"""\beval\s*\(|\bnew Function\s*\(|\bexec\s*\(""")),
        ("shell", re.compile(
            r"""child_process|\.exec\w*\(|os\.system|subprocess\.\w+\(.*shell\s*=\s*True|"""
            r"""Runtime\.getRuntime\(\)\.exec""", re.IGNORECASE)),
        # server fetch of a URL/host that may come from user input (SSRF candidate)
        ("ssrf", re.compile(
            r"""\b(axios|fetch|got|superagent|node-fetch|http\.request|https\.request|"""
            r"""requests\.(get|post|put|delete)|urllib\.request|urlopen|HttpClient|RestTemplate)\b"""
            r"""\s*\(.*\b(req|request|url|uri|target|host|endpoint|link|webhook|callback)\b""",
            re.IGNORECASE)),
    ],
    "authz": [
        ("id_lookup", re.compile(
            r"""(findById|find_by_id|get_object_or_404|\.get\(\s*(?:pk|id)\s*=|"""
            r"""findOne\(\s*\{\s*[_]?id|WHERE\s+id\s*=)""", re.IGNORECASE)),
        ("param_to_db", re.compile(
            r"""(req|request)\.(params|query|args)\b.*\b(find|get|query|select|delete|update)\b""",
            re.IGNORECASE)),
        # whole request body bound wholesale to a model (mass-assignment candidate)
        ("mass_assignment", re.compile(
            r"""(new\s+\w+\(\s*(req|request)\.(body|data)|"""
            r"""\.(create|update|insert|save|build)\(\s*(req|request)\.(body|data)|"""
            r"""Object\.assign\([^,]+,\s*(req|request)\.body|"""
            r"""\w+\(\s*\*\*\s*(request|req)\.(data|POST|json))""", re.IGNORECASE)),
    ],
    "secret": [
        ("hardcoded_secret", re.compile(
            r"""\b(api[_-]?key|secret|token|password|passwd|access[_-]?key|private[_-]?key)\b"""
            r"""\s*[:=]\s*['"][^'"\s]{8,}['"]""", re.IGNORECASE)),
        ("aws_key", re.compile(r"""\bAKIA[0-9A-Z]{16}\b""")),
    ],
    "cost": [
        ("select_star", re.compile(r"""SELECT\s+\*""", re.IGNORECASE)),
        ("unbounded_query", re.compile(
            r"""\.(findAll|find|all)\(\s*(\{\s*\}\s*)?\)|\.scan\(""")),
        # collection read with no obvious limit — a missing-pagination candidate
        ("missing_pagination", re.compile(
            r"""\.objects\.all\(\)|\.(findMany|findAll)\(\s*\)|\.list\(\s*\)""", re.IGNORECASE)),
    ],  # n_plus_1 is added by the loop-aware pass below
    "perf": [
        ("sync_io", re.compile(r"""readFileSync|writeFileSync|execSync|time\.sleep\(|Thread\.sleep""")),
    ],
    "correctness": [
        ("swallowed_error", re.compile(r"""catch\s*\([^)]*\)\s*\{\s*\}|except[^:]*:\s*pass\b""")),
        ("loose_equality", re.compile(r"""[^=!<>]==[^=]|!=[^=]""")),
        ("unseeded_id", re.compile(r"""Math\.random\(\).*\b(id|token|key|uuid)\b""", re.IGNORECASE)),
    ],
}

LOOP_HEADER = re.compile(r"""\b(for|while)\b|\.(forEach|map|each)\s*\(""")
AWAIT_CALL = re.compile(r"""\bawait\b|\.then\s*\(|\.query\(|\.fetch\(|requests?\.(get|post)""")


def run_git(repo: Path, args: list[str]) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), *args],
                             capture_output=True, text=True, check=True)
        return out.stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"git failed: {' '.join(args)}\n{exc}", file=sys.stderr)
        return ""


def is_code(path: str) -> bool:
    if IGNORE_RE.search(path):
        return False
    return Path(path).suffix in CODE_EXT


def list_files(repo: Path, mode: str) -> list[str]:
    if mode == "git":
        paths = run_git(repo, ["ls-files"]).splitlines()
    else:
        paths = [p.relative_to(repo).as_posix()
                 for p in repo.rglob("*") if p.is_file()]
    return sorted(p for p in paths if is_code(p))


def read_lines(repo: Path, rel: str) -> list[str]:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def excerpt(line: str) -> str:
    return line.strip()[:160]


def scan_file(rel: str, lines: list[str], families: set) -> list[dict]:
    out = []
    loop_window = 0  # >0 means we are within a few lines of a loop header
    for i, line in enumerate(lines, 1):
        for fam, pats in PATTERNS.items():
            if fam not in families:
                continue
            for subtype, rx in pats:
                if rx.search(line):
                    out.append({"family": fam, "subtype": subtype,
                                "cite": f"{rel}:{i}", "excerpt": excerpt(line),
                                "confidence": "candidate"})
        # loop-aware N+1 (cost): an awaited/query call within a loop body, whether
        # on the loop header line itself (`for (…) { await … }`) or a few lines below
        if "cost" in families:
            in_loop_header = bool(LOOP_HEADER.search(line))
            if in_loop_header:
                loop_window = 6
            if (in_loop_header or loop_window > 0) and AWAIT_CALL.search(line):
                out.append({"family": "cost", "subtype": "n_plus_1",
                            "cite": f"{rel}:{i}", "excerpt": excerpt(line),
                            "confidence": "candidate"})
            if not in_loop_header and loop_window > 0:
                loop_window -= 1
    return out


def build(repo: Path, args) -> dict:
    families = ({x.strip() for x in args.families.split(",") if x.strip()}
                if args.families != "all" else set(PATTERNS))
    files = list_files(repo, args.discovery)
    rows: list = []
    per_family: dict = defaultdict(int)
    for rel in files:
        for row in scan_file(rel, read_lines(repo, rel), families):
            if per_family[row["family"]] >= args.max_per_family:
                continue
            per_family[row["family"]] += 1
            rows.append(row)
    sha = run_git(repo, ["rev-parse", "--short", "HEAD"]).strip()
    return {
        "schema": "tornhill.signals/v1",
        "project": str(repo),
        "derived_from": sha,
        "families": sorted(families),
        "counts": dict(sorted(per_family.items())),
        "note": "candidates only — the audit skill confirms or drops each against real code",
        "signals": rows,
    }


def to_md(data: dict) -> str:
    L = [f"# risk signals (candidates) — {data['project']}",
         f"_families: {', '.join(data['families'])} · counts: {data['counts']}_\n",
         "> Every row is a CANDIDATE — confirm against real code before it becomes a finding.\n",
         "| family | subtype | cite | excerpt |",
         "|--------|---------|------|---------|"]
    for r in data["signals"]:
        ex = r["excerpt"].replace("|", "\\|")
        L.append(f"| {r['family']} | {r['subtype']} | `{r['cite']}` | `{ex}` |")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan candidate risk sinks (cite-or-confirm).")
    ap.add_argument("project")
    ap.add_argument("--families", default="all")
    ap.add_argument("--discovery", choices=["git", "walk"], default="git")
    ap.add_argument("--max-per-family", type=int, default=200)
    ap.add_argument("--format", choices=["json", "md"], default="json")
    args = ap.parse_args()

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 1
    data = build(repo, args)
    print(to_md(data) if args.format == "md" else json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
