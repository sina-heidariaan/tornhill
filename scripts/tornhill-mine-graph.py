#!/usr/bin/env python3
"""
tornhill-mine-graph — derive a deterministic MODULE-LEVEL import graph so the
"churn × centrality hotspot" finding stops being LLM-reasoned.

This is Tier 0: regex import extraction, no LSP / MCP / network, ~0 LLM tokens.
Coarse by design — tornhill needs module-altitude DEGREE (who has high fan-in /
fan-out within the repo), not call-exact edges. The altitude rule already discards
leaf detail, so import-level resolution at module granularity is sufficient.

  nodes  : modules (dir-prefix clustered at --module-depth) with in/out degree
  edges  : module -> module import edges, each citing a real `path:line`

Grounding contract ("cite or drop"): every edge cites the import statement line.
Imports that resolve outside the repo (external packages, unresolved relatives,
path aliases) are DROPPED and COUNTED in `dropped`, never guessed. The emitted
`graph.json` (schema tornhill.graph/v1) is the producer-agnostic contract a Tier-1
graph tool (LSP / code-graph MCP) can also satisfy with centrality_quality:"precise".

v1 resolves Python and TypeScript/JavaScript. Go/Java/Rust are a fast-follow.

Pure stdlib. Read-only. No network. Operates on ONE git repo.

Usage:
    python tornhill-mine-graph.py <project-dir> [options]

Options:
    --module-depth <n>   path components that define a "module" (default 2).
                         MUST match the tornhill-mine-git.py run it is joined with.
    --lang <list>        comma list of languages to resolve (default: py,ts)
    --discovery git|walk how files are found (default git = `git ls-files`,
                         which respects .gitignore and matches the churn universe)
    --min-weight <n>     mermaid: hide edges below this import weight (default 1)
    --top <n>            rows in the md degree table (default 15)
    --format json|md|mermaid   default json
"""
from __future__ import annotations

import argparse
import json
import posixpath
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

# --- shared with tornhill-mine-git.py (copied verbatim to keep each script standalone) ---
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


# --- graph-specific ---
TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
# extension-guess order for a bare relative TS/JS specifier
TS_GUESS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
            "/index.ts", "/index.tsx", "/index.js", "/index.jsx"]

PY_IMPORT = re.compile(r"^\s*import\s+([a-zA-Z_][\w.]*(?:\s*,\s*[a-zA-Z_][\w.]*)*)")
PY_FROM = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+")
TS_PATTERNS = [
    re.compile(r"""^\s*import\s+(?:[^'"]*?\sfrom\s+)?["']([^"']+)["']"""),
    re.compile(r"""^\s*export\s+[^'"]*?\sfrom\s+["']([^"']+)["']"""),
    re.compile(r"""\brequire\(\s*["']([^"']+)["']\s*\)"""),
    re.compile(r"""\bimport\(\s*["']([^"']+)["']\s*\)"""),
]


def list_files(repo: Path, mode: str) -> list[str]:
    if mode == "git":
        raw = run_git(repo, ["ls-files"])
        paths = raw.splitlines()
    else:
        paths = [p.relative_to(repo).as_posix()
                 for p in repo.rglob("*") if p.is_file()]
    return sorted(p for p in paths if is_code(p))


def read_text(repo: Path, rel: str) -> str:
    try:
        return (repo / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def lang_of(path: str) -> str | None:
    suf = Path(path).suffix
    if suf == ".py":
        return "py"
    if suf in TS_EXT:
        return "ts"
    return None


class Index:
    """Repo file set + resolution roots, computed once."""

    def __init__(self, files: list[str]):
        self.fileset = set(files)
        init_dirs = {posixpath.dirname(f) for f in files
                     if posixpath.basename(f) == "__init__.py"}
        # a python source root is the parent of a top-level package dir
        roots = {""}
        for d in init_dirs:
            parent = posixpath.dirname(d)
            if parent not in init_dirs:
                roots.add(parent)
        self.py_roots = roots

    def has(self, path: str) -> bool:
        return path in self.fileset


def iter_py_imports(text: str):
    """Yield (lineno, kind, payload). kind 'abs' -> dotted str;
    'rel' -> (level:int, module:str)."""
    for i, line in enumerate(text.splitlines(), 1):
        m = PY_FROM.match(line)
        if m:
            dots, module = m.group(1), m.group(2)
            if dots:  # leading dots => relative; no dots => absolute from-import
                yield i, "rel", (len(dots), module)
            elif module:
                yield i, "abs", module
            continue
        m = PY_IMPORT.match(line)
        if m:
            for name in m.group(1).split(","):
                name = name.strip()
                if name:
                    yield i, "abs", name


def iter_ts_imports(text: str):
    """Yield (lineno, specifier)."""
    for i, line in enumerate(text.splitlines(), 1):
        for pat in TS_PATTERNS:
            for m in pat.finditer(line):
                yield i, m.group(1)


def resolve_py(kind: str, payload, src: str, index: Index):
    """Return (dst_path|None, reason). reason in external|unresolved_relative."""
    if kind == "rel":
        level, module = payload
        base = posixpath.dirname(src)
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        if module:
            stem = posixpath.join(base, module.replace(".", "/"))
            for cand in (stem + ".py", posixpath.join(stem, "__init__.py")):
                if index.has(cand):
                    return cand, ""
        else:  # from . import x  -> the package __init__
            cand = posixpath.join(base, "__init__.py")
            if index.has(cand):
                return cand, ""
        return None, "unresolved_relative"

    segs = payload.split(".")
    for k in range(len(segs), 0, -1):  # longest dotted prefix first
        mod = "/".join(segs[:k])
        for root in index.py_roots:
            stem = posixpath.join(root, mod) if root else mod
            for cand in (stem + ".py", posixpath.join(stem, "__init__.py")):
                if index.has(cand):
                    return cand, ""
    return None, "external"


def resolve_ts(spec: str, src: str, index: Index):
    """Return (dst_path|None, reason). reason in external|unresolved_relative|alias."""
    if not spec.startswith("."):
        # bare specifier; a tsconfig path alias (@app/..) is indistinguishable
        # from an npm package without parsing tsconfig -> count honestly.
        return None, ("alias" if spec.startswith("@") else "external")
    base = posixpath.dirname(src)
    cand = posixpath.normpath(posixpath.join(base, spec))
    tries = []
    suf = Path(cand).suffix
    if suf in TS_EXT:
        tries.append(cand)
        # TS projects import a ".js" specifier that resolves to ".ts" source
        stem = cand[: -len(suf)]
        tries += [stem + e for e in (".ts", ".tsx")]
    else:
        tries += [cand + g for g in TS_GUESS]
    for t in tries:
        if index.has(t):
            return t, ""
    return None, "unresolved_relative"


def extract_edges(repo: Path, files: list[str], index: Index, langs: set[str]):
    raw_edges = []  # (src_file, dst_file, cite)
    dropped = defaultdict(int)
    for f in files:
        lang = lang_of(f)
        if lang not in langs:
            continue
        text = read_text(repo, f)
        if lang == "py":
            for lineno, kind, payload in iter_py_imports(text):
                dst, reason = resolve_py(kind, payload, f, index)
                if dst:
                    raw_edges.append((f, dst, f"{f}:{lineno}"))
                else:
                    dropped[reason] += 1
        else:  # ts
            for lineno, spec in iter_ts_imports(text):
                dst, reason = resolve_ts(spec, f, index)
                if dst:
                    raw_edges.append((f, dst, f"{f}:{lineno}"))
                else:
                    dropped[reason] += 1
    return raw_edges, dict(dropped)


def aggregate(raw_edges, files: list[str], depth: int):
    files_per_mod = defaultdict(set)
    for f in files:
        files_per_mod[module_of(f, depth)].add(f)

    weight = defaultdict(int)
    cite = {}
    out_neighbors = defaultdict(set)
    in_neighbors = defaultdict(set)
    for src_f, dst_f, c in raw_edges:
        ms, md = module_of(src_f, depth), module_of(dst_f, depth)
        if ms == md:  # intra-module import, not a coupling signal
            continue
        key = (ms, md)
        weight[key] += 1
        if key not in cite:  # first (lowest-lineno, files sorted) representative
            cite[key] = c
        out_neighbors[ms].add(md)
        in_neighbors[md].add(ms)

    mod_ids = sorted(set(files_per_mod) | {k[0] for k in weight} | {k[1] for k in weight})
    nodes = []
    for m in mod_ids:
        di, do = len(in_neighbors[m]), len(out_neighbors[m])
        if di == 0 and do == 0:
            continue  # isolated module — no coupling to show at this altitude
        nodes.append({
            "id": m.replace("/", "_"), "path": m, "kind": "module",
            "files": len(files_per_mod.get(m, ())),
            "degree_in": di, "degree_out": do, "degree": di + do,
        })
    edges = [{
        "src": s.replace("/", "_"), "dst": d.replace("/", "_"),
        "kind": "import", "weight": weight[(s, d)], "cite": cite[(s, d)],
    } for (s, d) in sorted(weight)]
    return nodes, edges


def build(repo: Path, args) -> dict:
    langs = {x.strip() for x in args.lang.split(",") if x.strip()}
    files = list_files(repo, args.discovery)
    index = Index(files)
    raw_edges, dropped = extract_edges(repo, files, index, langs)
    nodes, edges = aggregate(raw_edges, files, args.module_depth)
    sha = run_git(repo, ["rev-parse", "--short", "HEAD"]).strip()
    return {
        "schema": "tornhill.graph/v1",
        "project": str(repo),
        "derived_from": sha,
        "producer": "import-miner",
        "centrality_quality": "approx",
        "module_depth": args.module_depth,
        "languages": sorted(langs),
        "dropped": dropped,
        "nodes": nodes,
        "edges": edges,
    }


def to_md(data: dict, top: int) -> str:
    d = data["dropped"]
    drops = ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"
    L = [f"# import graph — {data['project']}",
         f"_producer: {data['producer']} · centrality_quality: "
         f"{data['centrality_quality']} · module_depth: {data['module_depth']} · "
         f"dropped({drops})_\n",
         "## Module degree (centrality)\n",
         "| module | files | in | out | degree |",
         "|--------|-------|----|----|--------|"]
    for n in sorted(data["nodes"], key=lambda x: -x["degree"])[:top]:
        L.append(f"| `{n['path']}` | {n['files']} | {n['degree_in']} | "
                 f"{n['degree_out']} | {n['degree']} |")
    L += ["\n## Edges (cited)\n", "| src | dst | weight | cite |",
          "|-----|-----|--------|------|"]
    by_w = sorted(data["edges"], key=lambda e: -e["weight"])[:top]
    id2path = {n["id"]: n["path"] for n in data["nodes"]}
    for e in by_w:
        L.append(f"| `{id2path.get(e['src'], e['src'])}` | "
                 f"`{id2path.get(e['dst'], e['dst'])}` | {e['weight']} | `{e['cite']}` |")
    L.append("\n> centrality from regex import resolution (Tier 0, approx); "
             "treat degree as directional, not exact.")
    return "\n".join(L) + "\n"


def to_mermaid(data: dict, min_weight: int) -> str:
    nodes = data["nodes"]
    degrees = sorted(n["degree"] for n in nodes)
    # mark "hot" hubs: degree strictly above the median (robust to one outlier on
    # small/uniform graphs, where a quartile index lands back in the mass)
    median = degrees[len(degrees) // 2] if degrees else 0
    suppress_leaves = len(nodes) > 12
    keep = {}
    for n in nodes:
        if suppress_leaves and n["degree"] <= 1:
            continue
        keep[n["id"]] = n
    L = ["flowchart TB"]
    for nid, n in keep.items():
        label = n["path"].split("/")[-1] or n["path"]
        hot = ":::hot" if n["degree"] > median and n["degree"] >= 2 else ""
        L.append(f"  {nid}[{label}]{hot}")
    for e in data["edges"]:
        if e["weight"] < min_weight:
            continue
        if e["src"] in keep and e["dst"] in keep:
            L.append(f"  {e['src']} --> {e['dst']}")
    for nid, n in keep.items():
        L.append(f'  click {nid} href "{n["path"]}"')
    L.append("  classDef hot fill:#ffe3e3,stroke:#cf222e,stroke-width:2px;")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine a deterministic module import graph.")
    ap.add_argument("project", help="path to the target project (a git repo)")
    ap.add_argument("--module-depth", type=int, default=2)
    ap.add_argument("--lang", default="py,ts")
    ap.add_argument("--discovery", choices=["git", "walk"], default="git")
    ap.add_argument("--min-weight", type=int, default=1)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--format", choices=["json", "md", "mermaid"], default="json")
    args = ap.parse_args()

    repo = Path(args.project).resolve()
    if not (repo / ".git").exists():
        print(f"not a git repo: {repo}", file=sys.stderr)
        return 1
    data = build(repo, args)
    if args.format == "md":
        print(to_md(data, args.top))
    elif args.format == "mermaid":
        print(to_mermaid(data, args.min_weight))
    else:
        print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
