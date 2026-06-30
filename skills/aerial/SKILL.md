---
name: aerial
description: >
  Generate an "astronaut's view that is still analyzable" of a project's
  architecture — a high-altitude system map whose HEADLINE output is a critique
  layer (single points of failure, hidden coupling, edge-case risks, recurring
  pains) pinned onto a structure + flow substrate, all grounded in real code and
  git history. Stops before code-level detail. Output: a version-controlled
  Mermaid + markdown source of truth plus an interactive HTML twin (zoom,
  click-node-to-code, findings toggle). Use when the user types "/aerial", says
  "map the architecture", "give me the system view", "where are the weaknesses",
  "show me the flows", or "architecture overview".
argument-hint: "[project-dir] [generate|refresh|flows] (default: generate)"
---

# aerial Skill

Produces a critique-led architecture view. The diagram is the substrate; **the
findings overlay is the product.** Drawing boxes without the critique layer is
the failure mode this skill exists to avoid.

> Design: critique-led · Mermaid source + interactive HTML twin · flows
> auto-proposed then user-pinned · one-shot on demand with a provenance stamp.

## ⛔ Grounding contract (overrides everything)

An invented architecture map **lies** — authoritatively. So:

- **Every box and every arrow cites a real `path:line`.** Derive edges from
  actual imports/calls/routes using whatever code-search or code-graph tools you
  have (ripgrep, an LSP, a code-graph MCP). Never infer an edge from a name.
- **A node or edge with no citation is dropped.** Unique-or-drop.
- **Every finding cites its evidence** — a `path:line`, a churn count, or a
  co-change pair from `aerial-mine-git.py`. No "probably / likely / typically".
- Code + git are ground truth. When the map and the code disagree, fix the map.

## Altitude rule (the hard part — enforce by COLLAPSING)

Build the full graph, then collapse so it stays analyzable:

- **L1 System Context** — the system + external actors/integrations.
- **L2 Containers / runtime units** — deployables, datastores, caches, queues.
- **L3 Components** — modules within a unit. **Cluster by directory/module; hide
  edges below a coupling threshold; suppress leaf utilities.** This collapse is
  what yields the spaceship altitude.
- **Hard stop before L4.** Never draw classes/functions/code in the diagram.

## Operations

| Invocation | Does |
|---|---|
| `/aerial [dir]` | generate the full view (default op) |
| `/aerial [dir] refresh` | regenerate + restamp `derived-from` commit |
| `/aerial [dir] flows` | re-run only flow selection/tracing |

One blueprint per runtime project. If the repo holds several, ask which.

## Pipeline

1. **Discover** — runtime units, entrypoints, stack (cheap recon, parallel).
2. **Derive structure** — build the module/call graph with your code-search /
   code-graph tools. Cluster into L1/L2/L3. Drop uncited edges.
3. **Mine git** — the deterministic differentiator:
   ```bash
   python scripts/aerial-mine-git.py <dir> --format json > /tmp/aerial-git.json
   ```
   (churn, co_change, growth, fix_hotspots.)
4. **Select flows** — rank entrypoints by reach; tag auth / money / external /
   state-mutating paths. **Propose a shortlist; the user pins / excludes.**
5. **Trace flows** — trace each pinned entrypoint into a sequence (L2/L3 only).
6. **Analyze** — run the findings taxonomy (below) over structure + flows + git
   signals. Pin each finding to its node/edge. Attach evidence.
7. **Render** — write the Mermaid + markdown source under `aerial/<dir>/`, then:
   ```bash
   python scripts/aerial-to-html.py aerial/<dir>/index.md --code-link vscode
   ```
8. **Stamp** — header records `derived-from <sha>` (`git -C <dir> rev-parse --short HEAD`).

## Findings taxonomy (the critique layer — pin + cite each)

- **Structural** — god-module (high in+out degree), circular module dependency,
  layering violation, shotgun-surgery cluster.
- **Resilience** — single point of failure, missing error boundary, sync call on
  a hot path that should be queued, unbounded fan-out / arch-level N+1.
- **Coupling / cohesion** — feature scattered across modules; **hidden coupling**
  = cross-module `co_change` pairs from the miner; leaky abstraction.
- **Flow-level** — retried path without idempotency, external call without
  timeout/circuit-breaker, auth check missing on a branch, money / state mutation
  without a transaction boundary.
- **Evolution (git)** — **churn × centrality hotspot** (high `churn` AND high
  graph degree = the real risk surface); **god-class trajectory** (top `growth`);
  **recurring pain** (top `fix_hotspots`).

> The two git-only families (hidden coupling, churn×centrality) are the headline
> differentiators — they cannot come from a static snapshot. Lead with them.

## Output structure (`aerial/<unit>/`)

- `index.md` — provenance header, the structure diagrams (`flowchart` for
  reliable click-to-code, or `C4*` if your renderer supports it), a
  severity-ordered **`## Findings`** section (each item tagged
  `**[blocker|high|medium|low]**` and pinned to a node), and a `## Flows`
  section with one `sequenceDiagram` per pinned flow.
- `index.html` — generated interactive twin (do not hand-write).

Authoring notes:

- Add `click <NodeId> href "<repo-relative-path>"` on component nodes so the HTML
  twin is click-to-code. The renderer rewrites these to `vscode://` / `file://` /
  GitHub links.
- Wrap the critique in a `## Findings` H2 and tag each item with its severity so
  the renderer can color + toggle it.

## Return format

A terse report: the blueprint path(s), the top 3–5 findings (severity + one line
+ evidence), the commit stamp, and the print-this HTML command. The analysis
lives in the files, not the reply. Never auto-commit.
