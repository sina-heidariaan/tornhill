#!/usr/bin/env python3
"""
tornhill-mine-symbols — a SCOPED symbol-level (L4) graph for the `/tornhill deep` op.

The global view hard-stops before L4. Deep mode relaxes that ONLY inside a
risk-selected, user-confirmed scope: this script parses **only files under
`--scope`**, so the L4 graph is structurally incapable of escaping the risk zone.

  nodes  : symbols (functions / classes / methods) declared in the scope, each
           citing a real `path:line`, with intra-scope in/out degree
  edges  : symbol -> symbol call references INSIDE the scope, each citing `path:line`

Grounding contract ("cite or drop"): a reference edge is emitted only when the
referenced name resolves to exactly ONE declared symbol in the scope. Names that
resolve to nothing in-scope (external / out-of-scope) or to more than one symbol
(ambiguous) are DROPPED and COUNTED, never guessed. Symbol resolution is regex-based
and coarser than module resolution, so `centrality_quality` is stamped "approx".

v1 resolves Python and TypeScript/JavaScript declarations. Pure stdlib. Read-only.
No network. Operates on ONE git repo.

Usage:
    python tornhill-mine-symbols.py <project-dir> --scope <module-path> [options]

Options:
    --scope <path>       REQUIRED. only files at/under this repo-relative prefix are
                         parsed (e.g. src/orders). Fences L4 to the risk zone.
    --top <n>            keep the top-N symbols by intra-scope degree (default 40)
    --lang <list>        comma list of languages to resolve (default: py,ts)
    --discovery git|walk how files are found (default git = `git ls-files`)
    --format json|mermaid   default json (mermaid emits the L4 flowchart)
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

# --- shared with tornhill-mine-graph.py (copied verbatim to keep each script standalone) ---
IGNORE_RE = re.compile(
    r"(^|/)(node_modules|dist|build|\.next|Pods|DerivedData|\.venv|vendor|"
    r"package-lock\.json|yarn\.lock|uv\.lock|Podfile\.lock|\.lock)(/|$)"
)
CODE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".py", ".swift", ".go", ".rb", ".java",
    ".kt", ".rs", ".php", ".cs", ".scala", ".m", ".mm", ".c", ".cpp", ".h",
}
TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


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


def lang_of(path: str) -> str | None:
    suf = Path(path).suffix
    if suf == ".py":
        return "py"
    if suf in TS_EXT:
        return "ts"
    return None


def in_scope(path: str, scope: str) -> bool:
    scope = scope.rstrip("/")
    return path == scope or path.startswith(scope + "/") or path.startswith(scope + ".")


def list_files(repo: Path, mode: str) -> list[str]:
    if mode == "git":
        paths = run_git(repo, ["ls-files"]).splitlines()
    else:
        paths = [p.relative_to(repo).as_posix()
                 for p in repo.rglob("*") if p.is_file()]
    return sorted(p for p in paths if is_code(p))


def read_text(repo: Path, rel: str) -> str:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# --- declaration patterns (group 1 = symbol name) ---
PY_DECLS = [
    ("class", re.compile(r"^(\s*)class\s+([A-Za-z_]\w*)")),
    ("function", re.compile(r"^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)")),
]
TS_DECLS = [
    ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")),
    ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
]
# a bare `NAME(` call site. Attribute/method calls (`obj.NAME(`) are excluded at
# match time so `posixpath.join(...)` never resolves to a local `join` symbol.
CALL_RE = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
# control-flow / operator keywords that are followed by `(` but are not calls
CALL_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "with", "elif",
    "function", "def", "class", "await", "yield", "typeof", "super", "and",
    "or", "not", "in", "new", "throw", "case", "do", "else",
}


def declarations(text: str, lang: str):
    """Yield (lineno, kind, name, is_method) for each declaration."""
    decls = PY_DECLS if lang == "py" else TS_DECLS
    for i, line in enumerate(text.splitlines(), 1):
        for kind, pat in decls:
            m = pat.match(line)
            if m:
                indent = m.group(1) if (lang == "py") else ""
                name = m.group(2) if (lang == "py") else m.group(1)
                is_method = bool(indent) if lang == "py" else False
                yield i, ("method" if (kind == "function" and is_method) else kind), name
                break


class Symbol:
    __slots__ = ("id", "path", "line", "name", "kind")

    def __init__(self, sid, path, line, name, kind):
        self.id, self.path, self.line, self.name, self.kind = sid, path, line, name, kind


def sanitize(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", token)


def collect_symbols(repo: Path, files: list[str], langs: set[str]):
    """Return (symbols list, by_file dict path->[(line,name,sym)], name_index name->[sym])."""
    symbols = []
    by_file = defaultdict(list)
    name_index = defaultdict(list)
    counter = 0
    for f in files:
        lang = lang_of(f)
        if lang not in langs:
            continue
        text = read_text(repo, f)
        for lineno, kind, name in declarations(text, lang):
            counter += 1
            sid = f"s{counter}_{sanitize(name)}"
            sym = Symbol(sid, f, lineno, name, kind)
            symbols.append(sym)
            by_file[f].append((lineno, name, sym))
            name_index[name].append(sym)
    for f in by_file:
        by_file[f].sort()
    return symbols, by_file, name_index


def enclosing(by_file_entries, line: int):
    """The declaration whose start line is the greatest <= `line` (approx nesting)."""
    found = None
    for dline, _name, sym in by_file_entries:
        if dline <= line:
            found = sym
        else:
            break
    return found


def extract_edges(repo: Path, files: list[str], langs: set[str], by_file, name_index):
    weight = defaultdict(int)
    cite = {}
    dropped = defaultdict(int)
    for f in files:
        lang = lang_of(f)
        if lang not in langs:
            continue
        entries = by_file.get(f, [])
        decl_lines = {(dline, name) for dline, name, _ in entries}
        text = read_text(repo, f)
        for i, line in enumerate(text.splitlines(), 1):
            for m in CALL_RE.finditer(line):
                name = m.group(1)
                start = m.start(1)
                if start > 0 and line[start - 1] == ".":
                    continue  # obj.NAME( — a method/attribute call, not this symbol
                if name in CALL_KEYWORDS:
                    continue  # if(/for(/return( ... — control flow, not a call
                if (i, name) in decl_lines:
                    continue  # the declaration itself, not a call
                targets = name_index.get(name)
                if not targets:
                    continue  # not a symbol declared in scope -> external, ignore silently
                if len(targets) > 1:
                    dropped["ambiguous"] += 1
                    continue
                src = enclosing(entries, i)
                if src is None:
                    dropped["no_enclosing"] += 1
                    continue
                dst = targets[0]
                if src.id == dst.id:
                    continue  # self / recursion — not an inter-symbol edge
                key = (src.id, dst.id)
                weight[key] += 1
                if key not in cite:
                    cite[key] = f"{f}:{i}"
    return weight, cite, dict(dropped)


def build(repo: Path, args) -> dict:
    langs = {x.strip() for x in args.lang.split(",") if x.strip()}
    all_files = list_files(repo, args.discovery)
    files = [f for f in all_files if in_scope(f, args.scope)]
    symbols, by_file, name_index = collect_symbols(repo, files, langs)
    weight, cite, dropped = extract_edges(repo, files, langs, by_file, name_index)

    out_n = defaultdict(set)
    in_n = defaultdict(set)
    for (s, d) in weight:
        out_n[s].add(d)
        in_n[d].add(s)

    ranked = sorted(
        symbols,
        key=lambda s: -(len(in_n[s.id]) + len(out_n[s.id])),
    )
    # suppress isolated symbols, then keep the top-N by intra-scope degree
    kept = [s for s in ranked if (len(in_n[s.id]) + len(out_n[s.id])) > 0][: args.top]
    kept_ids = {s.id for s in kept}
    if not kept:  # scope with no resolved intra-edges — still show declarations
        kept = ranked[: args.top]
        kept_ids = {s.id for s in kept}

    nodes = [{
        "id": s.id, "path": s.path, "line": s.line, "name": s.name,
        "kind": "symbol", "symbol_kind": s.kind,
        "cite": f"{s.path}:{s.line}",
        "degree_in": len(in_n[s.id]), "degree_out": len(out_n[s.id]),
        "degree": len(in_n[s.id]) + len(out_n[s.id]),
    } for s in kept]
    edges = [{
        "src": s, "dst": d, "kind": "call",
        "weight": weight[(s, d)], "cite": cite[(s, d)],
    } for (s, d) in sorted(weight) if s in kept_ids and d in kept_ids]

    sha = run_git(repo, ["rev-parse", "--short", "HEAD"]).strip()
    return {
        "schema": "tornhill.graph/v1",
        "level": "L4",
        "project": str(repo),
        "scope": args.scope.rstrip("/"),
        "derived_from": sha,
        "producer": "symbol-miner",
        "centrality_quality": "approx",
        "languages": sorted(langs),
        "files_in_scope": len(files),
        "symbols_total": len(symbols),
        "dropped": dropped,
        "nodes": nodes,
        "edges": edges,
    }


def to_mermaid(data: dict) -> str:
    nodes = data["nodes"]
    degrees = sorted(n["degree"] for n in nodes)
    median = degrees[len(degrees) // 2] if degrees else 0
    keep = {n["id"]: n for n in nodes}
    L = [f"flowchart TB",
         f"  %% deep dive — {data['scope']} (L4, approx)"]
    for nid, n in keep.items():
        label = f"{n['name']}"
        hot = ":::hot" if n["degree"] > median and n["degree"] >= 2 else ""
        L.append(f"  {nid}[{label}]{hot}")
    for e in data["edges"]:
        if e["src"] in keep and e["dst"] in keep:
            L.append(f"  {e['src']} --> {e['dst']}")
    for nid, n in keep.items():
        L.append(f'  click {nid} href "{n["cite"]}"')
    L.append("  classDef hot fill:#ffe3e3,stroke:#cf222e,stroke-width:2px;")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine a scoped symbol-level (L4) graph.")
    ap.add_argument("project", help="path to the target project (a git repo)")
    ap.add_argument("--scope", required=True,
                    help="repo-relative module prefix; only files here are parsed")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--lang", default="py,ts")
    ap.add_argument("--discovery", choices=["git", "walk"], default="git")
    ap.add_argument("--format", choices=["json", "mermaid"], default="json")
    args = ap.parse_args()

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 1
    data = build(repo, args)
    if args.format == "mermaid":
        print(to_mermaid(data))
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
