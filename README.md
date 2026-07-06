# tornhill

**A critique-led, code- and git-grounded architecture and audit toolkit for Claude Code.**

Most "architecture diagram generators" fail one of two ways: they draw every
class (a hairball) or three generic boxes (trivial). `tornhill` aims for the
analyzable middle altitude — high enough to see the whole system, low enough to
spot the storms — and its **headline output is not the diagram, it's the critique
overlay**: single points of failure, hidden coupling, hotspots, performance /
efficiency / correctness pains, and edge-case risks, each pinned to a node and
cited to real code or git history.

It ships **two skills**:

- **`/tornhill`** — the architecture view (structure + flows + findings), with a
  `deep` mode that auto-ranks the hottest subsystems and zooms into the real pains.
- **`/audit`** — a security · privacy/compliance · legal · cost review that grounds
  each finding in real code *and* a real, named standard (GDPR, HIPAA/FHIR,
  PCI-DSS, ISO 20022/PSD2, OWASP, …) selected for the app's domain and jurisdiction.

> An astronaut's view of Earth — but annotated with where the weather is bad.

```
/tornhill ./my-service
```

→ produces `tornhill/my-service/index.md` (Mermaid + findings) and an interactive
`index.html` (zoom · click a node to open its file · toggle the findings overlay).

---

## Install

> **Keep Claude Code up to date first** (`claude update`, or reinstall). Older
> versions reject the plugin source type (*"This plugin uses a source type your
> Claude Code version does not support"*) and can't show the component summary in
> the install preview.

**Remote (recommended)** — in Claude Code:

```
/plugin marketplace add sina-heidariaan/tornhill
/plugin install tornhill@tornhill
```

**Local / offline fallback** — works on any recent version, no network needed:

```
git clone https://github.com/sina-heidariaan/tornhill
```

then, in Claude Code:

```
/plugin marketplace add ./tornhill
/plugin install tornhill@tornhill
```

Installing adds two skills — **`/tornhill`** and **`/audit`**.

**Requirements:** `git` and Python 3.9+ (stdlib-first). The HTML renderer
optionally uses `markdown` + `pyyaml`.

---

## What makes it different

The building blocks have mature prior art — and tornhill stands on their shoulders:

- Git **temporal coupling** and **hotspot** analysis are Adam Tornhill's ideas
  ([Code Maat](https://github.com/adamtornhill/code-maat), *"Your Code as a Crime
  Scene"*). `tornhill-mine-git.py` is a small, dependency-free take on the same
  signals — and the project is **named in his honor** (see [Credits](#credits)).
- C4-style structure and dependency views exist in tools like Repowise, Emerge,
  and C4-InterFlow.

**tornhill's novelty is the layer on top:** an LLM turns those metrics + the
structure + traced flows into a **reasoned, prioritized, cited findings overlay**,
delivered **natively inside the Claude Code agent loop** as a `/command`. Other
tools hand you a CSV of coupling numbers; tornhill tells you *which coupling is a
risk, why, where, and how severe* — and, in the deep and audit modes, points at
the exact `path:line` that hurts.

It is **not** a new code-analysis engine. It's the critique + ergonomics layer.

### One rule at a time (the rule engine)

Both skills share a **rule engine**. Every analysis rule is an isolated, versioned
**rule card** in [`rules/`](rules/) — its intent, the evidence it consumes, the exact
confirmation steps, its false-positive guards, and its severity + standard mapping.
A deterministic router (`tornhill-route-rules.py`) hands each rule *only its own
evidence*, and the skill confirms **one rule at a time**. Rules never share a
prompt, so the model can't blend two families or invent a finding by mixing them —
the separation is the accuracy mechanism. Adding a rule is a YAML card, not a prose
edit; see [`docs/rules.md`](docs/rules.md).

The commands are unchanged — `/tornhill` loads the architecture rules, `/audit`
loads the security/privacy/cost rules; the engine is the shared backend.

See [`examples/benchmark.md`](examples/benchmark.md) for a sanitized run of the
engine against four real, private production backends — real engine numbers, with
nothing that could identify the projects (no framework, domain, or client code).

---

## Install

### As a Claude Code plugin (recommended)

The repo is its own plugin marketplace. Inside Claude Code:

```
/plugin marketplace add sina-heidariaan/tornhill
/plugin install tornhill@tornhill
```

Once installed, `/tornhill` and `/audit` are available in any session, in any
project — the skills invoke the bundled scripts via `${CLAUDE_PLUGIN_ROOT}`, so
your working directory stays the target you point them at. (`/plugin` also opens a
browser UI for the same thing.)

To develop against a local clone instead of GitHub:

```bash
git clone https://github.com/sina-heidariaan/tornhill
# then, inside Claude Code:
#   /plugin marketplace add /path/to/tornhill
#   /plugin install tornhill@tornhill
```

### Standalone (scripts only, no Claude needed for the deterministic parts)

```bash
pip install markdown pyyaml          # only the HTML renderer needs these
python scripts/tornhill-mine-git.py ./my-service --format md      # git signals
python scripts/tornhill-mine-graph.py ./my-service --format md    # module import graph
python scripts/tornhill-to-html.py tornhill/my-service/index.md     # render HTML
```

**Requirements:** `git`, Python 3.9+. The miners/scanners are pure stdlib; only the
HTML renderer needs `markdown` + `pyyaml`.

> **Works at every agent-access level, ~0 tokens.** The structure graph and the
> churn × centrality ranking are produced by deterministic stdlib scripts, not by
> the LLM re-reading files. Users with a code-graph engine (an LSP, or
> [FlowCrafter / CodeGraph](https://github.com/sina-parsania/FlowCrafter)) can supply
> a higher-precision graph under the same contract, at far lower token cost — see
> [`docs/graph-providers.md`](docs/graph-providers.md).

---

## Usage

### The `/tornhill` skill (architecture)

```
/tornhill [project-dir] [generate|refresh|flows|deep]
```

| Op | Effect |
|---|---|
| `generate` (default) | full view: structure diagrams + traced flows + findings |
| `refresh` | regenerate and restamp the `derived-from` commit |
| `flows` | re-run only flow selection / tracing |
| `deep` | auto-rank the hottest subsystems, confirm with you, then draw **scoped L4 detail** (classes/functions/detailed sequences) for the real pains |

The agent discovers the structure, runs the git miner, **proposes the critical
flows for you to pin**, writes the analysis, and renders the HTML. In `deep` mode
it ranks subsystems by churn × centrality (plus co-change and recurring-fix
weight), you pin 1–3, and only those get code-level detail — the global view still
collapses.

### The `/audit` skill (security · privacy/compliance · legal · cost)

```
/audit [project-dir] [quick|deep]
```

| Op | Effect |
|---|---|
| `quick` (default) | repo-wide posture scan + compliance matrix + top findings |
| `deep` | per-route/per-endpoint inspection: for each API route, checks authN/authZ, PII in responses and logs, input validation/injection, compliance clauses touched, and cost drivers |

The audit **first establishes the app's domain and jurisdiction(s)** (auto-detected
from dependencies and config, then confirmed by you), selects the applicable
standards, and grounds every finding in both a real `path:line` **and** a real
standard clause — or labels it `unmapped`. See
[`docs/audit-standards.md`](docs/audit-standards.md) for the catalog contract.

### `tornhill-mine-git.py` — deterministic git signals

```
python scripts/tornhill-mine-git.py <project-dir> [options]
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

### `tornhill-mine-graph.py` — deterministic module import graph (Tier 0)

```
python scripts/tornhill-mine-graph.py <project-dir> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--module-depth <n>` | `2` | path segments that define a "module" (match the git miner) |
| `--lang <list>` | `py,ts` | languages to resolve (v1: Python, TS/JS) |
| `--discovery git\|walk` | `git` | `git ls-files` (respects `.gitignore`) or filesystem walk |
| `--min-weight <n>` | `1` | `mermaid`: hide edges below this import weight |
| `--format json\|md\|mermaid` | `json` | `mermaid` emits the L3 module flowchart |

Emits `graph.json` (`schema: tornhill.graph/v1`): module nodes with in/out **degree**
(centrality) and cited `module → module` import edges. Resolves only in-repo
imports; external/aliased/unresolved are **dropped and counted** (`approx`, honest).
`--format mermaid` is the auto-drawn, click-to-code, altitude-collapsed L3 diagram.

### `tornhill-mine-symbols.py` — scoped symbol-level graph (L4, for `deep`)

```
python scripts/tornhill-mine-symbols.py <project-dir> --scope <module-path> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--scope <path>` | — | **required.** only files under this prefix are parsed (fences L4 to the risk zone) |
| `--top <n>` | `40` | keep the top-N symbols by intra-scope degree |
| `--lang <list>` | `py,ts` | languages to resolve |
| `--format json\|mermaid` | `json` | `mermaid` emits the L4 flowchart |

Extracts class/function/method declarations and intra-scope references (each cited
to a real `path:line`) into `graph.symbols.json` (`tornhill.graph/v1`,
`kind:"symbol"`, `centrality_quality:"approx"`). Unresolved refs are dropped +
counted. It **cannot** emit nodes outside `--scope`.

### `tornhill-mine-routes.py` — deterministic route enumerator (for `/audit`)

```
python scripts/tornhill-mine-routes.py <project-dir> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--frameworks <list>` | `auto` | limit to specific frameworks, or auto-detect |
| `--format json\|md` | `json` | output format |

Enumerates route declarations across Express/Fastify/NestJS, Flask/FastAPI,
Django, Spring, Rails, and Next.js API routes into `routes.json`
(`tornhill.routes/v1`): each row cites `path:line`, HTTP method, URL pattern, handler
location, and statically-attached middleware. Dynamic/unresolved routes are
**dropped and counted**. It only *enumerates* — it does not trace handler
internals (the audit skill does that, grounded by the signals below).

### `tornhill-scan-signals.py` — deterministic candidate-sink scanner

```
python scripts/tornhill-scan-signals.py <project-dir> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--families <list>` | `all` | subset of `pii,log,injection,authz,secret,cost,perf,correctness` |
| `--format json\|md` | `json` | output format |

Greps cite-able **candidate** risk sinks into `signals.json` (`tornhill.signals/v1`):
PII fields near responses, request/response logging, string-concatenated SQL /
`eval` / `child_process`, missing-auth hints, hardcoded secrets, and
cost/perf/correctness smells (N+1, unbounded queries, sync calls on hot paths).
**Everything here is a candidate, not a verdict** — the skill confirms each against
the real code before it becomes a finding, and drops the rest.

### `tornhill-route-rules.py` — deterministic rule router (the engine core)

```
python scripts/tornhill-route-rules.py [--rules <dir>] --signals signals.json \
  --routes routes.json [--git git.json] [--risk risk.json] [--graph graph.json] \
  [--pack arch_pack|audit_pack|all] [--top <n>] [--format json|md]
```

| Option | Default | Meaning |
|---|---|---|
| `--rules <dir>` | bundled `rules/` | the rule catalog to route against |
| `--pack <name>` | `all` | which command's families to load (`arch_pack` / `audit_pack`) |
| `--top <n>` | `25` | cap evidence rows per rule |
| `--format json\|md` | `json` | output format |

Reads the rule catalog plus the deterministic candidate outputs and emits
`ruleplan.json` (`tornhill.ruleplan/v1`): a list of **per-rule work packets**, each
carrying only the evidence that rule's `consumes` tags match. This is what lets the
skill analyze one rule at a time. Missing inputs → empty buckets (never invented).

### `tornhill-sanitize.py` — leak-free run summary

```
python scripts/tornhill-sanitize.py --ruleplan ruleplan.json [--routes …] \
  [--signals …] [--git …] [--risk …] --label "Repo X" --stack "…" [--format md|json]
```

Turns a real run's artifacts into a publishable summary: keeps aggregate counts and
tornhill's own rule ids, **drops every excerpt, and pseudonymizes all paths/module
names**. Used to produce [`examples/benchmark.md`](examples/benchmark.md) without
exposing any client code.

### `tornhill-join-risk.py` — deterministic churn × centrality

```
python scripts/tornhill-join-risk.py --git <git.json> --graph <graph.json> [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--git <path>` | — | `tornhill-mine-git.py` JSON (required) |
| `--graph <path>` | — | `tornhill-mine-graph.py` JSON, or any `tornhill.graph/v1` (required) |
| `--score percentile\|minmax\|product` | `percentile` | risk formula (percentile-rank product) |
| `--top <n>` | `15` | rows |
| `--format json\|md` | `json` | output format |

Ranks hotspot modules by **percentile-rank product** of churn × degree — rank-based,
so it's outlier-robust and the product enforces the AND (a module must be high on
*both* axes). Refuses to join if the two inputs used different `--module-depth`.

### `tornhill-to-html.py` — interactive renderer

```
python scripts/tornhill-to-html.py [files...] [options]
```

| Option | Default | Meaning |
|---|---|---|
| `--out-dir <dir>` | `tornhill` | where blueprints live (when no files given) |
| `--code-link vscode\|file\|github` | `vscode` | how clicking a node opens code |
| `--repo-root <dir>` | CWD | root for resolving `github` links |
| `--github-base <url>` | — | e.g. `https://github.com/org/repo/blob/main` |
| `--assets cdn\|inline\|local` | `cdn` | where the viewer JS comes from (see [Security & trust](#security--trust)) |
| `--vendor-dir <dir>` | `vendor` | vendored JS for `inline`/`local` |

`vscode` emits `vscode://file/...` (opens in your editor — the browser asks once
to allow). `github` is for HTML you share with teammates.

---

## How it works (pipeline)

1. **Discover** runtime units, entrypoints, stack.
2. **Derive structure** from real imports/calls/routes; cluster to L1/L2/L3;
   drop any edge it can't cite.
3. **Mine git** (`tornhill-mine-git.py`) for the signals a snapshot can't show.
4. **Select flows** — auto-propose the critical ones (auth / money / highest
   reach); you pin or exclude.
5. **Trace** each pinned flow into a sequence diagram.
6. **Route rules → analyze one at a time** (`tornhill-route-rules.py`) — each rule
   gets only its own evidence; the skill confirms each in isolation, pinned + cited.
7. **Render** the Mermaid source + interactive HTML twin.
8. **Stamp** the commit it was derived from.

### The rules

The analysis rules live as isolated cards in [`rules/`](rules/), grouped into a
critical set across four family groups:

- **Structural / resilience / coupling / evolution** — god-module, circular dep,
  layering violation, SPOF, unbounded fan-out, hidden co-change coupling,
  churn × centrality hotspot, god-class trajectory, recurring-fix pain.
- **Security** — IDOR/BOLA, missing auth, SQL/exec injection, SSRF, hardcoded
  secret, mass assignment, missing rate-limit, insecure deserialization.
- **Privacy / compliance** — PII in responses/logs, over-collection, consent,
  retention/erasure, subject access — each mapped to a real standard clause.
- **Performance / efficiency / correctness** — N+1, sync-on-hot-path, missing
  timeout, unbounded/over-fetch/pagination, swallowed error, missing transaction,
  race condition, weak identifier.

Each card records its evidence sources, confirmation steps, false-positive guards,
and severity — see [`docs/rules.md`](docs/rules.md).

### The altitude rule

A diagram that shows everything shows nothing. tornhill builds the full graph then
**collapses**: clusters by module, hides low-weight edges, suppresses leaf
utilities, and **hard-stops before code level** — *except* inside a risk-selected,
user-confirmed `deep` scope, where code-level (L4) detail is allowed but fenced to
that scope. The collapse is the product.

---

## Security & trust

tornhill is designed to be auditable and safe to run on any machine:

- **Read-only.** It runs `git log` (via `subprocess` with list args — no shell,
  no injection) and reads source files. It never writes to your repo; it only
  writes blueprint files under `tornhill/`.
- **No network at generation time. No telemetry.** Nothing about your code leaves
  your machine. (The audit skill only reaches the network if you explicitly ask it
  to verify a standard clause against its authority URL.)
- **Small + auditable.** Each script is stdlib-only Python (plus `markdown`/`pyyaml`
  for rendering). Read them.
- **One third-party surface, your choice how to handle it:** the generated
  **HTML** needs two JS libraries (`mermaid`, `svg-pan-zoom`) to draw diagrams in
  the *viewer's* browser. `tornhill-to-html.py --assets` controls where they come
  from:

  | Mode | Third-party fetch at view time? | Output |
  |---|---|---|
  | `cdn` *(default)* | yes — pinned + **SRI-verified** CDN tags | small, single file |
  | `inline` | **no** | self-contained single file (~3.4 MB) |
  | `local` | **no** | small HTML + a `vendor/` dir beside it |

  Versions are pinned (not a floating range) and CDN tags carry
  `integrity`/`crossorigin` so a swapped payload fails loudly. For `inline`/`local`,
  populate `./vendor` once (offline-safe thereafter):

  ```bash
  python scripts/tornhill-vendor-assets.py        # downloads pinned, SRI-verified libs
  python scripts/tornhill-to-html.py tornhill/my-service/index.md --assets inline
  ```

  The vendor fetch verifies each download against the same SRI hash before
  writing, so it refuses to vendor tampered bytes. `vendor/` is git-ignored.

---

## Stack independence

tornhill is **language-agnostic.** The git miner filters by source extension and
works on any git repo (Go, Swift, Python, TS, Java, …). The structure derivation
relies on whatever code-search/code-graph tooling the agent has, which is
multi-language. There is nothing stack-specific in the engine.

---

## Credits

**The name is a tribute.** *tornhill* is named in honor of **Adam Tornhill**, whose
behavioral-code-analysis work — [Code Maat](https://github.com/adamtornhill/code-maat)
and *"Your Code as a Crime Scene"* (temporal coupling, hotspots) — is the foundation
of the git-signal engine here. The name is an homage, **not an endorsement**: this
project is independent and not affiliated with Adam Tornhill, Code Maat, or CodeScene.

## License

Dual-licensed, at your option, under either:

- [MIT license](LICENSE-MIT), or
- [Apache License 2.0](LICENSE-APACHE).

Unless you explicitly state otherwise, any contribution intentionally submitted for
inclusion in this project by you shall be dual-licensed as above, without any
additional terms or conditions.
