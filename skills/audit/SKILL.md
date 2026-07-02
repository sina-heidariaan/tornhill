---
name: audit
description: >
  Review a codebase for SECURITY, PRIVACY/COMPLIANCE, LEGAL, and COST risk — and
  ground every finding in real code AND a real, named standard clause (GDPR,
  HIPAA/FHIR, PCI-DSS, ISO 20022/PSD2, OWASP, ISO 27001, CCPA, WCAG) selected for
  the app's domain and jurisdiction. Two modes: a quick repo-wide posture scan, and
  a deep per-route/per-endpoint inspection (data leakage, PII in responses/logs,
  auth/token verification, broken access control, injection, compliance clauses
  touched, cost drivers). Output: a version-controlled markdown report + interactive
  HTML twin. Use when the user types "/audit", says "security review", "compliance
  check", "is this GDPR/HIPAA/PCI compliant", "check for data leakage", "audit the
  API", or "review the endpoints".
argument-hint: "[project-dir] [quick|deep] (default: quick)"
---

# audit Skill

Produces a **grounded** compliance/security/legal/cost review. A generic "you should
use HTTPS and validate input" checklist is the failure mode this skill exists to
avoid. Every finding must point at a real `path:line` AND cite a real standard
clause — or be labelled `unmapped`.

> Companion to `/tornhill` (architecture). Same ethos: deterministic miners →
> cite-able candidates → reasoned findings → markdown source + HTML twin.

## ⛔ Grounding contract (overrides everything)

Two anchors per finding — miss either and you drop or downgrade it:

- **Code anchor.** Every finding cites a real `path:line` (from `routes.json`,
  `signals.json`, or a handler trace). No "the app probably…". Candidates from
  `tornhill-scan-signals.py` are **candidates** — confirm each against the real code
  before it becomes a finding; drop the ones that aren't real.
- **Standard anchor.** A finding may attach a standard ONLY via a `standard_id +
  clause_id` that resolves in the bundled catalog (`${CLAUDE_PLUGIN_ROOT}/skills/audit/standards/`).
  If nothing in the catalog fits, either **downgrade to `unmapped`** (a real code
  issue with no standard attribution) or **verify the clause live** against the
  standard's `authority_url` (WebFetch) and stamp `verified_at` + URL. **Never invent
  a clause number or a regulation.** See `${CLAUDE_PLUGIN_ROOT}/docs/audit-standards.md`.

Surface `catalog_version` in the report so a reader sees how fresh the mapping is.

## Pipeline

### 1. Establish the profile (domain + jurisdiction) — auto-detect, then CONFIRM

The applicable standards depend entirely on *what the app is* and *where it
operates*. Get this right first.

- **Auto-detect** signals:
  - domain — dependencies (`stripe`/`braintree`/`square` → payments + card data;
    `fhir`/`hl7`/`smart-on-fhir` → healthcare; `plaid`/ISO 20022 libs → financial;
    ecommerce carts), directory/keyword hits.
  - jurisdiction/region — i18n locale dirs, currency/locale config, address/phone
    formats, cloud-region config, an existing privacy policy, company region.
- **Propose** a profile `{domains, jurisdictions, handles: [card_data, health_data,
  pii], has_ui}` and **ask the user to confirm or edit it.** Accept explicit
  overrides. If a committed `tornhill/audit/profile.yml` exists, offer to reuse it.
- **Persist** the confirmed profile to `tornhill/audit/profile.yml`, stamped with
  `catalog_version` + `derived-from`, so re-runs are reproducible.

Misdetecting the jurisdiction picks the wrong privacy regime — this confirm step is
mandatory, not optional.

### 2. Select the standard set

Read `${CLAUDE_PLUGIN_ROOT}/skills/audit/standards/index.yml` and resolve the profile through
`always` + `by_domain` + `by_region` + `by_condition` into a set of standard IDs.
Load only the standard files that resolve in `standards:`. A selected standard with
no catalog file → its findings map as `unmapped` (never guessed).

### 3. Enumerate routes + scan candidate signals (deterministic, ~0 tokens)

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-mine-routes.py <dir> --format json > /tmp/tornhill-routes.json
python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-scan-signals.py <dir> --format json > /tmp/tornhill-signals.json
```

`routes.json` (`tornhill.routes/v1`) lists every enumerable endpoint with its
`path:line`, method, handler location, static middleware, and a `has_auth` flag;
dynamic routes are dropped + counted. `signals.json` (`tornhill.signals/v1`) lists
cite-able candidate sinks by family (`pii`, `log`, `injection`, `authz`, `secret`,
`cost`, `perf`, `correctness`). Both are evidence to confirm — not findings.

Then **route the candidates to the isolated rule cards** (the engine's core):

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-route-rules.py --pack audit_pack \
  --signals /tmp/tornhill-signals.json --routes /tmp/tornhill-routes.json \
  --risk /tmp/tornhill-risk.json --format json > /tmp/tornhill-ruleplan.json
```

`ruleplan.json` (`tornhill.ruleplan/v1`) is a list of per-rule work packets, each
carrying ONLY that rule's routed evidence (`${CLAUDE_PLUGIN_ROOT}/rules/*.yml`,
`audit_pack` families: security, privacy, perf, correctness — see
`${CLAUDE_PLUGIN_ROOT}/docs/rules.md`).

### 4. Detect — walk the rule plan ONE rule at a time

Analysis is a per-rule loop, not a single all-in-one checklist. Walk
`ruleplan.json` rule by rule; each packet carries only that rule's routed evidence.
For **each** rule:

1. Read that ONE rule card — `intent`, `procedure`, `false_positive_guards`,
   `severity_criteria`. Hold no other rule in mind during the pass.
2. Run the card's `procedure` over its routed candidates + the real code; apply
   **every** `false_positive_guard`; drop anything you cannot anchor to a real
   `path:line`.
3. Emit that rule's confirmed **code issues** (code anchor only, no standard yet),
   tagged with the rule `id`. Then move to the next rule.

- **QUICK mode** — process rules in evidence-density order for a repo-wide posture;
  no per-endpoint trace.
- **DEEP mode** — additionally, for each state-mutating/sensitive route in
  `routes.json`, trace the handler (seed with the routed evidence whose `cite`
  falls in its file/range) and run that rule's `procedure` per endpoint.

Detection is deliberately kept **separate from clause mapping** — one rule, one
frame. This is what stops the audit from blending a code check with a legal
citation and guessing a clause.

### 5. Attach standards — a SEPARATE isolated pass

Only now, for each confirmed code issue, attach a legal anchor from its rule card's
`standard_map`:

- Cite a `standard_id + clause_id` **only if** the profile actually selected that
  standard (step 2) **and** the clause resolves in the catalog. The rule card's
  `standard_map` is the pre-computed candidate mapping — you still confirm the
  clause fits the concrete code.
- If no selected standard fits → **`unmapped`** (a real code issue, no attribution),
  or WebFetch-verify against the standard's `authority_url` and stamp `verified_at`.
  **Never invent a clause.** (See `${CLAUDE_PLUGIN_ROOT}/docs/audit-standards.md`.)

### 6. Rate and write

Assign severity from each rule's `severity_criteria` + the rubric below. Cross-weight
with git churn / `fix_hotspots` if a `/tornhill` run is available (a bug in a hot,
frequently-fixed file matters more), then write the report.

### 7. Render + stamp

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/tornhill-to-html.py tornhill/audit/index.md --code-link vscode
```

Header records `derived-from <sha>`. Never auto-commit.

## Findings taxonomy + severity

Families: **security · privacy · legal · cost** (plus **performance / efficiency /
correctness** carried over from the `/tornhill` perf/correctness rules when relevant).

Each finding records:

```
**[severity] family** — title
  evidence: path:line                          <- code anchor (required)
  standard: GDPR Art.5(1)(c) (catalog 2026.07) <- standard anchor, or `unmapped`
  endpoint: GET /api/orders/:id                <- deep mode
  why: one line grounded in the cited code
```

Severity rubric (applied to grounded evidence, never guessed):

- **blocker** — sensitive-data leak (PII/PHI/card) reachable from an endpoint; auth
  or authorization missing on a state-mutating or money route; injection reachable
  from user input.
- **high** — PII/secrets in logs; broken access control on a read route; missing
  input validation on external input; a clearly-mapped legal/compliance violation.
- **medium** — cost drivers (N+1 / unbounded query / expensive sync call on a hot
  path); weaker privacy gaps; misconfiguration.
- **low** — hygiene, non-reachable candidates, accessibility nits.

Severity reuses the `[blocker|high|medium|low]` vocabulary so `tornhill-to-html.py`
colors it unchanged.

## Output structure (`tornhill/audit/`)

- `profile.yml` — confirmed `{domains, jurisdictions, handles, has_ui}` + stamps.
- `index.md`:
  - frontmatter: `title`, `derived-from`, `profile`, `catalog_version`.
  - `## Profile` — domain + jurisdiction + the selected standard set (cited to
    `index.yml`).
  - `## Compliance matrix` — a table of standard × posture (`pass` / `gap` / `n/a`)
    with finding counts.
  - `## Findings` — severity-ordered, each tagged `**[blocker|high|medium|low]**`
    (so the renderer colors + toggles it), with both anchors.
  - `## Endpoints` *(deep mode)* — one `###` per route: method + path, auth status,
    per-endpoint findings, and an optional mini `sequenceDiagram` of the sensitive
    path.
- `index.html` — generated interactive twin (do not hand-write).

## Return format

A terse report: the profile (domain + jurisdiction), the selected standards, the
top 3–5 findings (severity + endpoint + evidence + clause), the compliance-matrix
summary, and the render command. The full analysis lives in the files, not the
reply. Never auto-commit.
