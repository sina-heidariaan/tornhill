---
name: tornhill
description: >
  Generate an "astronaut's view that is still analyzable" of a project's
  architecture — a high-altitude system map whose HEADLINE output is a critique
  layer (single points of failure, hidden coupling, performance/efficiency/
  correctness pains, edge-case risks, recurring pains) pinned onto a structure +
  flow substrate, all grounded in real code and git history. Stops before
  code-level detail, except inside a risk-selected, user-confirmed deep-dive
  scope. Output: a version-controlled Mermaid + markdown source of truth plus an
  interactive HTML twin (zoom, click-node-to-code, findings toggle). Use when the
  user types "/tornhill", says "map the architecture", "give me the system view",
  "where are the weaknesses", "show me the flows", "find the real pain points", or
  "architecture overview".
argument-hint: "[project-dir] [generate|refresh|flows|deep] (default: generate)"
---

# tornhill Skill

Produces a critique-led architecture view. The diagram is the substrate; **the
findings overlay is the product.** Drawing boxes without the critique layer is
the failure mode this skill exists to avoid.

> Design: critique-led · Mermaid source + interactive HTML twin · flows
> auto-proposed then user-pinned · one-shot on demand with a provenance stamp.

## ⛔ Grounding contract (overrides everything)

An invented architecture map **lies** — authoritatively. Every claim tornhill makes
must be checkable by opening real code. So:

- **Every box and every arrow carries an evidence anchor — a real `path:line`.**
  Derive edges from actual imports/calls/routes using whatever code-search or
  code-graph tools you have — `tornhill-mine-graph.py` (deterministic Tier-0 import
  graph, needs no tooling), ripgrep, an LSP, or a code-graph MCP. Never infer an
  edge from a name.
- **No anchor, no node/edge.** If you cannot cite it, you drop it. A smaller true
  map beats a larger plausible one.
- **Every finding cites its evidence** — a `path:line`, a churn count, or a
  co-change pair from `tornhill-mine-git.py`. No "probably / likely / typically".
- Code + git are ground truth. When the map and the code disagree, fix the map.

The `path:line` anchor does double duty: it is the falsifiable proof of the claim,
and the renderer turns it into a click-to-open code link in the HTML twin.

## Altitude rule (the hard part — enforce by COLLAPSING)

Build the full graph, then collapse so it stays analyzable:

- **L1 System Context** — the system + external actors/integrations.
- **L2 Containers / runtime units** — deployables, datastores, caches, queues.
- **L3 Components** — modules within a unit. **Cluster by directory/module; hide
  edges below a coupling threshold; suppress leaf utilities.** This collapse is
  what yields the spaceship altitude.
- **Hard stop before L4 GLOBALLY.** Never draw classes/functions/code in the main
  diagrams. **Exception:** inside a risk-selected, user-confirmed **deep-dive
  scope** (the `deep` op), L4 (symbols + detailed sequences) is permitted, bounded
  by `tornhill-mine-symbols.py --scope`. The global L1–L3 view still collapses.

## Operations

| Invocation | Does |
|---|---|
| `/tornhill [dir]` | generate the full view (default op) |
| `/tornhill [dir] refresh` | regenerate + restamp `derived-from` commit |
| `/tornhill [dir] flows` | re-run only flow selection/tracing |
| `/tornhill [dir] deep` | auto-rank the hottest subsystems, confirm with the user, then draw scoped L4 detail for the pains |

One blueprint per runtime project. If the repo holds several, ask which.

## Pipeline

> **Scratch dir** — intermediate JSON goes to `tornhill/.cache/` (a project-relative
> path that resolves identically in bash and Python on every OS). Create it once
> before the first write: `mkdir -p tornhill/.cache`. **Do not use `/tmp`** — on
> Windows, bash maps `/tmp` to `%TEMP%` but Python reads it as `C:\tmp`, so the
> scripts can't find files the redirects wrote.
>
> **Cost** — a full run typically spends a few thousand tokens (the deterministic
> miners are ~0; the spend is the LLM critique + flow tracing). `deep` mode and the
> per-rule protocol scale higher. Tell the user before a large or `deep` run.

1. **Discover** — runtime units, entrypoints, stack (cheap recon, parallel).
2. **Derive structure** — build the module graph. Always run the deterministic
   Tier-0 miner (works with no special tooling, ~0 tokens):
   ```bash
   mkdir -p tornhill/.cache
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-graph.py <dir> --module-depth 2 --format json > tornhill/.cache/tornhill-graph.json
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-graph.py <dir> --module-depth 2 --format mermaid   # the L3 flowchart
   ```
   The `mermaid` output IS the `## L3 — Components` diagram (degree-weighted,
   click-to-code, altitude-collapsed) — paste it, don't hand-draw. If you have an
   LSP / code-graph MCP, you may instead emit `graph.json` at higher precision
   (`centrality_quality:"precise"`); see `${CLAUDE_PLUGIN_ROOT}/docs/graph-providers.md`. Cluster into
   L1/L2/L3. Drop uncited edges.
3. **Mine git** — the deterministic differentiator (use the **same `--module-depth`**
   as step 2; a high `--top` so module churn sums aren't truncated):
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-git.py <dir> --module-depth 2 --top 1000 --format json > tornhill/.cache/tornhill-git.json
   ```
   (churn, co_change, growth, fix_hotspots.) Then **join** churn × centrality
   deterministically:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-join-risk.py --git tornhill/.cache/tornhill-git.json --graph tornhill/.cache/tornhill-graph.json --format json > tornhill/.cache/tornhill-risk.json
   ```
4. **Scan pain signals** (optional but recommended for the perf/efficiency/
   correctness rules) —
   deterministic candidate sinks the critique layer confirms or drops:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-scan-signals.py <dir> --families cost,perf,correctness --format json > tornhill/.cache/tornhill-signals.json
   ```
5. **Select flows** — rank entrypoints by reach; tag auth / money / external /
   state-mutating paths. **Propose a shortlist; the user pins / excludes.**
6. **Trace flows** — trace each pinned entrypoint into a sequence (L2/L3 only).
7. **Route rules, then analyze one rule at a time.** Build the per-rule work plan
   deterministically, then confirm each rule *in isolation*:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-route-rules.py --pack arch_pack \
     --signals tornhill/.cache/tornhill-signals.json --graph tornhill/.cache/tornhill-graph.json \
     --git tornhill/.cache/tornhill-git.json --risk tornhill/.cache/tornhill-risk.json \
     --format json > tornhill/.cache/tornhill-ruleplan.json
   ```
   `ruleplan.json` (`tornhill.ruleplan/v1`) is a list of per-rule work packets, each
   carrying ONLY that rule's routed evidence. **Walk it one rule at a time** — see
   *Per-rule analysis protocol* below. Never analyze two rules in the same frame.
8. **Render** — write the Mermaid + markdown source under `tornhill/<dir>/`, then:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-to-html.py tornhill/<dir>/index.md --code-link vscode
   ```
9. **Stamp** — header records `derived-from <sha>` (`git -C <dir> rev-parse --short HEAD`).

## Deep-dive mode (`deep`) — find the REAL pains, then zoom

Goal: surface the genuinely painful/important parts, not generic boxes. Runs after
steps 1–7 (it needs `tornhill-risk.json` + git signals).

1. **Auto-rank** candidate deep-dive scopes deterministically — no free judgement:
   - `tornhill-risk.json` `hotspots[]` — `score = pct_rank(churn) × pct_rank(centrality)` (primary).
   - `co_change` clusters — modules that change together = a coupled subsystem.
   - `fix_hotspots` — recurring-fix weight (chronic pain).
2. **Present a shortlist** (top ~5 scopes with their risk numbers + one line on *why*).
   **The user pins 1–3.** Only pinned scopes get L4.
3. **Mine symbols, scoped** — for each pinned module prefix:
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-symbols.py <dir> --scope <module-path> --format json > tornhill/.cache/tornhill-symbols-<n>.json
   python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-symbols.py <dir> --scope <module-path> --format mermaid   # the L4 flowchart
   ```
   This parses **only files under the scope**, so L4 cannot escape the risk zone.
   Every symbol node/edge cites a real `path:line`; unresolved refs are dropped +
   counted; `centrality_quality:"approx"`.
4. **Run the per-rule analysis protocol** (below) inside the scope so the deep dive
   reports concrete pains ("this function — `path:line` — has issue Y"), each cited.
   Re-route with `--graph tornhill/.cache/tornhill-symbols-<n>.json` so the perf/efficiency/
   correctness rules see the scoped symbols.
5. **Render** a `## Deep dive — <module>` section per pinned scope: the L4
   `flowchart` + a detailed `sequenceDiagram` (every step cites a mined symbol).
   Same `tornhill-to-html.py` twin. The global L1–L3 view is untouched.

## Per-rule analysis protocol (the engine — work strictly one rule at a time)

The analysis rules are ISOLATED cards in `${CLAUDE_PLUGIN_ROOT}/rules/*.yml`
(`arch_pack` families: structural, resilience, coupling, evolution, perf,
efficiency, correctness — see `${CLAUDE_PLUGIN_ROOT}/docs/rules.md`).
`tornhill-route-rules.py` has already handed each rule only its own evidence. For
**each** rule packet in `ruleplan.json`, in order:

1. Read that ONE rule card — `intent`, `procedure`, `false_positive_guards`,
   `severity_criteria`. Hold no other rule in mind during the pass.
2. If `deterministic: true` (the evolution + hidden-coupling rules), read the
   finding straight off its evidence rows (`risk.json` / `git.json`) — **no
   reasoning**. Pin each to its module node by `path`; cite churn + degree (or
   co-change support) and stamp `centrality_quality` (`approx` for the Tier-0
   import graph, `precise` for an LSP/MCP graph).
3. Otherwise run the card's `procedure` against its routed candidates + the real
   code; apply **every** `false_positive_guard`; drop anything you cannot anchor to
   a real `path:line`.
4. Emit that rule's confirmed findings, each tagged with the rule `id`, its family,
   and a severity from `severity_criteria`. Then move to the next rule.

**Why one at a time:** a single prompt that runs the whole taxonomy at once blends
families and invents findings. Isolated passes keep every finding inside one rule's
frame — the separation *is* the accuracy mechanism.

After all rules: **merge** — dedup by (`path:line`, family), cross-weight severity
with git churn / `fix_hotspots` (a pain in a hot, frequently-fixed file matters
more), order by severity, and render.

> The deterministic git rules (hidden coupling, churn×centrality, growth,
> recurring-fix) are the headline differentiators — they cannot come from a static
> snapshot or a generic diagram. Lead with them.

## Output structure (`tornhill/<unit>/`)

- `index.md` — provenance header, the structure diagrams (`flowchart` for
  reliable click-to-code, or `C4*` if your renderer supports it), a
  severity-ordered **`## Findings`** section (each item tagged
  `**[blocker|high|medium|low]**` and pinned to a node), a `## Flows` section
  with one `sequenceDiagram` per pinned flow, and (in `deep` mode) one
  `## Deep dive — <module>` section per pinned scope.
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
