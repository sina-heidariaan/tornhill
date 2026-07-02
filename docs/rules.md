# Rules — the catalog contract

tornhill's analysis rules live in `rules/*.yml` as **isolated rule cards** — one
self-contained unit per rule. This is the deterministic backbone of the engine: a
router (`tornhill-route-rules.py`) hands each rule only its own evidence, and the
skill confirms **one rule at a time**. Rules never share a prompt, so the agent
cannot conflate two families or hallucinate a finding by blending them.

The same "cite or drop" discipline the `standards/` catalog uses for legal clauses
now governs every rule: no evidence anchor, no finding.

## Why cards, not prose

Previously the taxonomy and analysis lenses were inline prose in the skills, with
examples that bled across families (an arch-level N+1 lived under both *Resilience*
and *Performance*). A rule card gives each rule a strict, non-overlapping frame:
what it is, what evidence it consumes, the exact confirmation steps, the false-
positive guards, and its severity + standard mapping. The engine walks the cards
independently; the skill reasons within one card's boundary at a time.

## `index.yml` — registry + packs

```yaml
catalog_version: "2026.07"
families:                 # family -> catalog file (must resolve to be routable)
  security: security.yml
  privacy:  privacy.yml
  perf:     performance.yml
  # ...
packs:                    # which command loads which families
  arch_pack:  [structural, resilience, coupling, evolution, perf, efficiency, correctness]
  audit_pack: [security, privacy, perf, correctness]
```

- `families` — a rule is routable/citeable only if its family resolves to a file.
- `packs` — `/tornhill` loads `arch_pack`, `/audit` loads `audit_pack`. Only a
  pack's rules reach that command's per-rule loop, so the two skills stay separated
  even though they share the engine.

## Rule-card schema

```yaml
- id: SEC-IDOR-01                 # stable, unique; FAMILY-TOPIC-NN
  family: security                # optional in single-family files; required in mixed files
  title: Broken object-level authorization (IDOR / BOLA)
  intent: >                       # one paragraph: what the defect IS
    A handler loads an object by a user-supplied id and returns/mutates it without
    verifying the caller owns it.
  consumes:                       # join keys into the deterministic candidate outputs
    signals: [authz/id_lookup]    #   "<family>/<subtype>" from scan-signals
    routes:  [has_auth:false, state_mutating]   #   tokens matched against routes.json
    graph:   [degree, cycle]      #   graph.json structural tokens
    git:     [co_change, growth, fix_hotspots]  #   mine-git rows
    risk:    [hotspots]           #   join-risk rows
  procedure:                      # the STRICT steps the agent runs for THIS rule only
    - Open the handler at the candidate cite.
    - Confirm the id originates from user input.
    - Confirm the lookup has no ownership filter.
  false_positive_guards:          # explicit reasons to DROP a candidate
    - Ownership enforced by an upstream guard (trace it).
  evidence_required: [code_anchor]           # + standard_anchor for compliance rules
  severity_default: blocker
  severity_criteria: blocker if state-mutating/sensitive; else high.
  deterministic: false            # true = read straight off git/risk JSON, no reasoning
  standard_map:                   # optional bridge to standards/*.yml (audit)
    - {standard: OWASP-TOP10, clause: "A01:2021"}
```

Field notes:

- **`consumes`** is the router's contract. Each key names a producer; the value is a
  list of join tokens. `signals` tokens are `family/subtype` exactly as
  `tornhill-scan-signals.py` emits them. This is what lets the router build a
  per-rule evidence bucket with ~0 tokens.
- **`procedure` + `false_positive_guards`** are the isolated reasoning frame. The
  skill runs these for one rule, emits its findings, then moves on — it does not
  hold other rules in mind during the pass.
- **`deterministic: true`** (evolution/hotspot/coupling rules) means the finding is
  read straight off `risk.json` / `git.json` with no free judgement — the card just
  records where to read and how to pin it.
- **`standard_map`** attaches legal clauses as a **separate step** from code
  detection. A mapped clause is only cited if the profile actually selected that
  standard; otherwise the finding is `unmapped` (never guessed). Every clause id
  must resolve in `skills/audit/standards/*.yml` — the only citeable legal source
  (see `docs/audit-standards.md`).

## The router (`tornhill-route-rules.py`)

Consumes `signals.json`, `routes.json`, `git.json`/`risk.json`, and this catalog;
emits `ruleplan.json` (`tornhill.ruleplan/v1`): for each rule, the bucket of
candidate rows whose tags match its `consumes`. The skill loads the ruleplan and
walks it rule by rule. Deterministic rules arrive with their rows pre-attached.

## Adding a rule

1. Add a card to the right family file (or a new file wired into `families`).
2. Give it a unique `id`, a tight `intent`, real `consumes` tokens, a concrete
   `procedure`, and honest `false_positive_guards`. Prefer dropping a rule to
   shipping one that fires on false positives.
3. If it maps to a regulation, add `standard_map` entries whose clauses already
   exist in the catalog — never invent a clause.
4. Bump `catalog_version` in `index.yml` (and the family file).

## Coverage

Ships a curated **critical set** across four family groups:

| Group | Families | Rules |
|---|---|---|
| Security | security | IDOR, missing-auth, SQL/exec injection, SSRF, secret, mass-assignment, rate-limit, deserialization |
| Privacy | privacy | PII in response/logs, over-collection, consent, retention/erasure, access |
| Perf/Eff/Correctness | perf, efficiency, correctness | N+1, sync-hot-path, timeout, unbounded/over-fetch/pagination, swallowed-error, transaction, race, loose-eq, weak-id |
| Structural/Resilience/Evolution | structural, resilience, coupling, evolution | god-module, cycle, layering, SPOF, fan-out, hidden coupling, churn×centrality, growth, recurring-fix |

New rules and families are added over time; an incomplete catalog just yields more
honest `unmapped` / undetected gaps rather than invented findings.
