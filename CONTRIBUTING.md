# Contributing to tornhill

Thanks for helping. tornhill is small and deliberately auditable — keep it that way.

## Project shape

- `scripts/tornhill-mine-git.py` — deterministic git-signal miner. **Pure stdlib.**
  Read-only. No network. Don't add dependencies here.
- `scripts/tornhill-mine-graph.py` — deterministic Tier-0 module import graph
  (py+ts), emits `graph.json` (`tornhill.graph/v1`). **Pure stdlib.** No network.
- `scripts/tornhill-join-risk.py` — deterministic churn × centrality join. **Pure
  stdlib.** Reads the two miners' JSON; no repo access.
- `scripts/tornhill-to-html.py` — HTML renderer. May use `markdown` + `pyyaml` only.
- `scripts/tornhill-vendor-assets.py` — one-shot vendoring of viewer JS for offline
  HTML. **Pure stdlib.** The only script that touches the network, by design.
- `scripts/tornhill-mine-symbols.py` — deterministic **scoped** symbol-level (L4)
  graph miner for the `deep` op. **Pure stdlib.** No network.
- `scripts/tornhill-mine-routes.py` — deterministic route/endpoint enumerator for
  the `/audit` skill; emits `routes.json` (`tornhill.routes/v1`). **Pure stdlib.**
- `scripts/tornhill-scan-signals.py` — deterministic candidate-sink scanner (PII,
  logging, injection, secrets, cost/perf/correctness). Candidates only — the LLM
  confirms or drops. **Pure stdlib.**
- `scripts/tornhill-route-rules.py` — deterministic rule router: buckets candidates
  under the rule cards they feed, emits `ruleplan.json` (`tornhill.ruleplan/v1`).
  May use `pyyaml` (reads the catalog). No repo access.
- `scripts/tornhill-sanitize.py` — turns a real run into a leak-free summary (counts
  + rule ids only; strips excerpts, pseudonymizes paths). **Pure stdlib.**
- `rules/` — the **rule catalog**: isolated rule cards grouped by family, plus
  `index.yml` (registry + pack membership). The single source of truth for analysis
  rules; see [`docs/rules.md`](docs/rules.md).
- `skills/tornhill/SKILL.md` — the architecture skill (a prompt). Behavior, not code.
- `skills/audit/SKILL.md` — the audit skill (security/privacy/legal/cost);
  `skills/audit/standards/` — the bundled, curated standards catalog.
- `examples/` — synthetic + **sanitized** benchmark only. **Never commit real/private
  codebase data.** Use `tornhill-sanitize.py`; keep raw runs in git-ignored `internal/`.

## Setup

```bash
git clone https://github.com/sina-heidariaan/tornhill && cd tornhill
pip install markdown pyyaml        # renderer deps; the miner needs nothing
```

Requirements: `git`, Python 3.9+.

## Smoke test (run before every PR)

> Paths below use `/tmp/...` (Linux/macOS). On Windows, substitute a temp dir such
> as `%TEMP%\...` (cmd) or `$env:TEMP\...` (PowerShell).

```bash
# 1. git miner runs and emits the four sections
python scripts/tornhill-mine-git.py . --since "10 years ago" --module-depth 2 --top 1000 --format json > /tmp/git.json

# 1b. graph miner emits a valid tornhill.graph/v1; join ranks hotspots
python scripts/tornhill-mine-graph.py . --module-depth 2 --format json > /tmp/graph.json
python scripts/tornhill-mine-graph.py . --format mermaid          # L3 flowchart
python scripts/tornhill-join-risk.py --git /tmp/git.json --graph /tmp/graph.json --format json > /tmp/risk.json

# 1c. rule router buckets candidates into per-rule work packets
python scripts/tornhill-scan-signals.py . --format json > /tmp/signals.json
python scripts/tornhill-mine-routes.py  . --format json > /tmp/routes.json
python scripts/tornhill-route-rules.py --pack audit_pack \
  --signals /tmp/signals.json --routes /tmp/routes.json --git /tmp/git.json \
  --risk /tmp/risk.json --graph /tmp/graph.json --format md

# 2. renderer produces an .html next to the .md, all link modes work
python scripts/tornhill-to-html.py examples/orders-service/index.md
python scripts/tornhill-to-html.py examples/orders-service/index.md --code-link file
python scripts/tornhill-to-html.py examples/orders-service/index.md \
  --code-link github --github-base https://github.com/sina-heidariaan/tornhill/blob/main

# 3. open the html — diagrams render, zoom/pan works, "Toggle findings" works

# 4. (offline modes) vendor the libs once, then render self-contained / local
python scripts/tornhill-vendor-assets.py
python scripts/tornhill-to-html.py examples/orders-service/index.md --assets inline
python scripts/tornhill-to-html.py examples/orders-service/index.md --assets local
```

## Conventions

- **Read-only & offline at generation time.** The miner and renderer must not
  write to the analyzed repo (only under `tornhill/`) and must not hit the network.
  The only allowed third-party surface is the pinned CDN JS in the *output* HTML.
- **No shell injection.** `subprocess` always uses list args, never `shell=True`.
- **Grounding is sacred.** Anything the skill emits (box, edge, finding) must cite
  real code or a git signal. Features that would let it guess get rejected.
- **Small functions, stdlib-first, UTF-8 stdout.** Match the existing style.
- **Pin, don't float.** CDN/library versions are exact, never ranges.
- **Conventional commits** (`feat:`, `fix:`, `docs:`, `refactor:`, `chore:`).

## Adding a feature

1. Open an issue describing the finding/diagram/signal and *how it stays grounded*.
2. For a new **git signal**: add it to `tornhill-mine-git.py`'s `mine()` + both
   output formats, and update the README param table.
3. For a new **analysis rule**: add an isolated **rule card** to the right family
   file under `rules/` — a tight `intent`, real `consumes` tokens, a concrete
   `procedure`, honest `false_positive_guards`, and (if compliance) a `standard_map`
   whose clauses already exist in the catalog. Don't edit prose in the skills; the
   catalog is the source of truth. See [`docs/rules.md`](docs/rules.md).
4. For a new **language** in the import miner: add a `lang_of` suffix, an
   `iter_<lang>_imports` extractor + a `resolve_<lang>` that only resolves in-repo
   targets (drop+count everything else), and a fixture. Don't regress `approx`
   honesty. See [`docs/graph-providers.md`](docs/graph-providers.md) for the contract.
5. For a new **route/signal family** (audit): add the pattern to
   `tornhill-mine-routes.py` or `tornhill-scan-signals.py` as a **candidate** (never a
   verdict — the LLM confirms); wire its `family/subtype` into a rule card's
   `consumes`, and, for a compliance clause, add it to the catalog under
   `skills/audit/standards/` (see [`docs/audit-standards.md`](docs/audit-standards.md)).
6. For **renderer** changes: keep the HTML self-contained; test all three link
   modes and the findings toggle.
7. Update `README.md` and run the smoke test. Open a PR.

## What tornhill is NOT

Not a code-analysis engine to compete with Code Maat / Repowise — it's the
LLM-critique + Claude-native ergonomics layer on top. Keep contributions aligned
with that scope; deep static-analysis engines belong in a code-graph dependency,
not here.
