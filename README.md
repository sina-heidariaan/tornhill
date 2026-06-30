# aerial

**A critique-led, code- and git-grounded architecture view for Claude Code.**

Most "architecture diagram generators" fail one of two ways: they draw every
class (a hairball) or three generic boxes (trivial). `aerial` aims for the
analyzable middle altitude — high enough to see the whole system, low enough to
spot the storms — and its **headline output is not the diagram, it's the critique
overlay**: single points of failure, hidden coupling, hotspots, and edge-case
risks, each pinned to a node and cited to real code or git history.

> An astronaut's view of Earth — but annotated with where the weather is bad.

```
/aerial ./my-service
```

→ produces `aerial/my-service/index.md` (Mermaid + findings) and an interactive
`index.html` (zoom · click a node to open its file · toggle the findings overlay).

---

## What makes it different

The building blocks have mature prior art — and aerial stands on their shoulders:

- Git **temporal coupling** and **hotspot** analysis are Adam Tornhill's ideas
  ([Code Maat](https://github.com/adamtornhill/code-maat), *"Your Code as a Crime
  Scene"*). `aerial-mine-git.py` is a small, dependency-free take on the same
  signals.
- C4-style structure and dependency views exist in tools like Repowise, Emerge,
  and C4-InterFlow.

**aerial's novelty is the layer on top:** an LLM turns those metrics + the
structure + traced flows into a **reasoned, prioritized, cited findings overlay**,
delivered **natively inside the Claude Code agent loop** as a `/command`. Other
tools hand you a CSV of coupling numbers; aerial tells you *which coupling is a
risk, why, where, and how severe.*

It is **not** a new code-analysis engine. It's the critique + ergonomics layer.

---

## Install

### As a Claude Code plugin (recommended)

```bash
# clone next to your projects, or anywhere
git clone https://github.com/midxdle/aerial
```

Add it as a plugin (Claude Code auto-discovers `skills/`, and the
`.claude-plugin/plugin.json` manifest). Once loaded, `/aerial` is available in
any session. The two Python scripts live under `scripts/`.

### Standalone (scripts only, no Claude needed for the deterministic parts)

```bash
pip install markdown pyyaml          # only the HTML renderer needs these
python scripts/aerial-mine-git.py ./my-service --format md      # git signals
python scripts/aerial-to-html.py aerial/my-service/index.md     # render HTML
```

**Requirements:** `git`, Python 3.9+. The git miner is pure stdlib; only the HTML
renderer needs `markdown` + `pyyaml`.

---

## Usage

### The skill (inside a Claude session)

```
/aerial [project-dir] [generate|refresh|flows]
```

| Op | Effect |
|---|---|
| `generate` (default) | full view: structure diagrams + traced flows + findings |
| `refresh` | regenerate and restamp the `derived-from` commit |
| `flows` | re-run only flow selection / tracing |

The agent discovers the structure, runs the git miner, **proposes the critical
flows for you to pin**, writes the analysis, and renders the HTML.

### `aerial-mine-git.py` — deterministic git signals

```
python scripts/aerial-mine-git.py <project-dir> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--since <git-date>` | `12 months ago` | history window |
| `--max-commits <n>` | `4000` | cap on commits scanned |
| `--top <n>` | `15` | rows per section |
| `--module-depth <n>` | `2` | path segments that define a "module" |
| `--cochange-cap <n>` | `40` | skip commits touching more files than this (noise filter) |
| `--min-support <n>` | `3` | min shared commits for a co-change pair |
| `--format json\|md` | `json` | output format |

Sections: `churn` (change pressure), `co_change` (hidden cross-module coupling),
`growth` (god-class trajectory), `fix_hotspots` (recurring pain).

### `aerial-to-html.py` — interactive renderer

```
python scripts/aerial-to-html.py [files...] [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--out-dir <dir>` | `aerial` | where blueprints live (when no files given) |
| `--code-link vscode\|file\|github` | `vscode` | how clicking a node opens code |
| `--repo-root <dir>` | CWD | root for resolving `github` links |
| `--github-base <url>` | — | e.g. `https://github.com/org/repo/blob/main` |

`vscode` emits `vscode://file/...` (opens in your editor — the browser asks once
to allow). `github` is for HTML you share with teammates.

---

## How it works (pipeline)

1. **Discover** runtime units, entrypoints, stack.
2. **Derive structure** from real imports/calls/routes; cluster to L1/L2/L3;
   drop any edge it can't cite.
3. **Mine git** (`aerial-mine-git.py`) for the signals a snapshot can't show.
4. **Select flows** — auto-propose the critical ones (auth / money / highest
   reach); you pin or exclude.
5. **Trace** each pinned flow into a sequence diagram.
6. **Analyze** — produce the findings overlay (taxonomy below), each pinned + cited.
7. **Render** the Mermaid source + interactive HTML twin.
8. **Stamp** the commit it was derived from.

### Findings taxonomy

Structural (god-module, circular dep, layering violation) · Resilience (SPOF,
missing error boundary, sync-on-hot-path, unbounded fan-out) · Coupling (hidden
cross-module co-change, feature scatter) · Flow-level (no idempotency on retry,
no timeout on external call, missing auth branch, no transaction boundary) ·
Evolution (churn × centrality hotspot, god-class trajectory, recurring pain).

### The altitude rule

A diagram that shows everything shows nothing. aerial builds the full graph then
**collapses**: clusters by module, hides low-weight edges, suppresses leaf
utilities, and **hard-stops before code level**. The collapse is the product.

---

## Security & trust

aerial is designed to be auditable and safe to run on any machine:

- **Read-only.** It runs `git log` (via `subprocess` with list args — no shell,
  no injection) and reads source files. It never writes to your repo; it only
  writes blueprint files under `aerial/`.
- **No network at generation time. No telemetry.** Nothing about your code leaves
  your machine.
- **Small + auditable.** Each script is < 300 lines of stdlib-only Python (plus
  `markdown`/`pyyaml` for rendering). Read them.
- **One third-party surface:** the generated **HTML** loads two pinned JS
  libraries (`mermaid`, `svg-pan-zoom`) from a CDN to draw diagrams in the
  *viewer's* browser. They are version-pinned (not a floating range). If you need
  fully offline / zero-CDN output, vendor those two files locally and point the
  `<script>` tags at them — see the roadmap.

---

## Stack independence

aerial is **language-agnostic.** The git miner filters by source extension and
works on any git repo (Go, Swift, Python, TS, Java, …). The structure derivation
relies on whatever code-search/code-graph tooling the agent has, which is
multi-language. There is nothing stack-specific in the engine.

---

## Roadmap

- [ ] Vendored / SRI-pinned JS for fully offline, zero-CDN HTML.
- [ ] Optional single-binary git miner (Go) for dependency-free distribution.
- [ ] `churn × centrality` auto-join when a code-graph is available.
- [ ] More diagram backends (C4, structurizr export).

---

## Credits

Behavioral-code-analysis concepts (temporal coupling, hotspots) are inspired by
**Adam Tornhill** — [Code Maat](https://github.com/adamtornhill/code-maat) and
*"Your Code as a Crime Scene"*.

## License

[MIT](LICENSE)
