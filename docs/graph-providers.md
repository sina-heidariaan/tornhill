# Graph providers — the `tornhill.graph/v1` contract

tornhill needs a **module-altitude import/call graph** to compute the
`churn × centrality hotspot` finding deterministically (via
[`scripts/tornhill-join-risk.py`](../scripts/tornhill-join-risk.py)). Rather than
build a code-analysis engine, tornhill defines a small **producer-agnostic contract**
— `graph.json` — and lets the best tool you have produce it.

> **Why a contract, not an engine.** The join reads only node degrees,
> `module_depth`, and `centrality_quality`. It never inspects `producer`. So a
> zero-token regex miner and a full LSP/MCP graph engine join *identically* — only
> the stamped precision changes. Swap the producer, keep the pipeline.

## Tiers

| Tier | Producer | Tokens | `centrality_quality` | Precision |
|---|---|---|---|---|
| **0 — floor (everyone)** | [`tornhill-mine-graph.py`](../scripts/tornhill-mine-graph.py) — regex import miner | ~0 (subprocess) | `approx` | module import degree |
| **1 — opt-in** | an LSP or code-graph MCP (e.g. [FlowCrafter / CodeGraph](https://github.com/sina-parsania/FlowCrafter)) | a few k | `precise` | real call edges, resolved types |

Tier 0 requires nothing but Python + git and works for any user at any agent-access
level. Tier 1 is a precision upgrade when the user already has the tooling.

## The schema (`schema: "tornhill.graph/v1"`)

```json
{
  "schema": "tornhill.graph/v1",
  "project": "<resolved repo path>",
  "derived_from": "<short sha>",
  "producer": "import-miner | lsp | mcp:<name>",
  "centrality_quality": "approx | precise",
  "module_depth": 2,
  "languages": ["py", "ts"],
  "dropped": { "external": 412, "unresolved_relative": 7, "alias": 3 },
  "nodes": [
    { "id": "src_orders", "path": "src/orders", "kind": "module",
      "files": 6, "degree_in": 3, "degree_out": 2, "degree": 5 }
  ],
  "edges": [
    { "src": "src_cart", "dst": "src_orders", "kind": "import",
      "weight": 4, "cite": "src/cart/cart.service.ts:3" }
  ]
}
```

### Producer requirements (any tier)

1. **Module-collapsed nodes.** Emit nodes at `module_depth` (the dir-prefix module,
   `"/".join(path.split("/")[:depth])`). A producer that natively works at
   file/function granularity MUST aggregate to module nodes so IDs reconcile with
   the git miner's churn and the Mermaid `click` targets. Use the same depth the
   git miner ran with — the join refuses a mismatch.
2. **`id` = `path.replace("/", "_")`**, **`path`** = repo-relative module dir. The
   join keys on `path`; Mermaid keys on `id`.
3. **`degree_in` / `degree_out`** = count of **distinct neighbor modules** (structural
   fan), `degree` = their sum. `weight` (underlying edge count) lives on edges only.
4. **Cite or drop.** Every edge carries a real `cite` (`path:line`). Anything that
   can't be resolved to an in-repo target is dropped and counted in `dropped`, never
   guessed. `dropped` keeps the `approx` honest — a reader sees coverage at a glance.
5. **Stamp `centrality_quality`.** `approx` for heuristic resolution (regex imports),
   `precise` for type-resolved graphs. This flag propagates through the join into the
   finding's evidence, so the precision caveat travels with the data.

## Recommended Tier-1 producer — FlowCrafter / CodeGraph

[FlowCrafter / CodeGraph](https://github.com/sina-parsania/FlowCrafter) is a small
(~5 MB) Rust binary that runs as **both an MCP server and a CLI**. It indexes a
repo into a resolved knowledge graph across 13 languages with **`file:line`
precision**, **drops ambiguous references rather than guessing** (the same
cite-or-drop discipline tornhill uses), and answers structural questions with
~21–100× fewer tokens than reading files with grep. That makes it an ideal Tier-1
producer: instead of the Tier-0 regex miner re-deriving structure, the agent asks
CodeGraph for the resolved call/import edges once.

Conceptual mapping to `tornhill.graph/v1` (produced by the agent from CodeGraph's
output — no per-file exploration):

| tornhill.graph/v1 field | from CodeGraph |
|---|---|
| nodes (modules) | its symbol/file nodes, **aggregated to `module_depth`** (group by dir prefix) |
| edges (`kind:"import"` / `"call"`) | its resolved import/call edges, rolled up to module→module |
| `degree_in/out` | distinct neighbor modules after the roll-up |
| `cite` | the `file:line` of a representative edge (CodeGraph already carries it) |
| `centrality_quality` | `"precise"` |
| `producer` | `"mcp:codegraph"` |

Because CodeGraph edges are already `file:line`-precise and ambiguity-dropped, the
roll-up is a one-time, bounded transform and the **join consumes the result
unchanged** — same risk math, same finding, the caveat simply flips from `approx`
to `precise`.

> A turnkey adapter (a script that emits `tornhill.graph/v1` directly from CodeGraph)
> is not shipped yet — today the mapping above is done by the agent. Tier-0
> (`tornhill-mine-graph.py`) remains the zero-setup default.

## Accuracy of the Tier-0 floor (honest `approx`)

Regex import resolution at module altitude is intentionally coarse. Realistic ceilings:

- **Python ~90–95%** — static `import`/`from` dominate; misses `importlib`/`__import__`
  and namespace packages without `__init__.py`.
- **TS/JS ~70–85%** — relative specifiers resolve well; the big hole is tsconfig
  `paths`/`baseUrl` aliases (dropped + counted as `alias`), and dynamic
  `import(`./${x}`)` template literals (unresolvable).

Two reasons this is sufficient:

1. **tornhill only needs relative degree.** "Who has high fan-in/out *within this repo*"
   survives uniform under-resolution.
2. **The join uses ranks, not magnitudes.** Percentile-rank product is invariant to
   uniform error — a mildly under-counted module keeps its rank. A module's risk only
   moves if its resolution error is *differential* vs. the rest of the repo.

When you need exactness, supply a Tier-1 graph; the pipeline doesn't change.
