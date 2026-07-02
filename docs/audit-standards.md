# Audit standards — the catalog contract

The `/audit` skill grounds every compliance/legal finding in a **real, named
standard clause** — the same "cite or drop" discipline tornhill uses for code. The
bundled catalog under `skills/audit/standards/` is the **only citeable
source of clause identifiers**. This is what stops the audit from inventing
regulations.

## The rule

A finding may attach a standard **only** via a `standard_id + clause_id` that
resolves in the catalog. If the model wants to cite something not in the catalog it
must either:

1. **downgrade** the finding to `unmapped` (a real code issue, no standard
   attribution), or
2. **verify** the clause live against the standard's `authority_url` (WebFetch) and
   record `verified_at` + the source URL — it never invents a clause number.

Every finding surfaces the `catalog_version` so a reader sees how fresh the mapping
is (the same spirit as `centrality_quality: approx` on the graph).

## `index.yml` — selection

`index.yml` maps the confirmed project profile (domain + jurisdiction) to the
applicable standard IDs:

```yaml
catalog_version: "2026.07"
always: [OWASP-TOP10, ISO-27001]          # every project
by_domain:
  healthcare: [HIPAA, HL7-FHIR]
  payments:   [PCI-DSS]
  financial_eu: [PSD2, ISO-20022]
by_region:
  EU: [GDPR]
  UK: [UK-GDPR]
  US-CA: [CCPA]
by_condition:
  handles_card_data: [PCI-DSS]
  has_ui: [WCAG]
standards:                                 # id -> file (must exist to be citeable)
  GDPR: gdpr.yml
  CCPA: ccpa.yml
  HIPAA: hipaa.yml
  PCI-DSS: pci-dss.yml
  OWASP-TOP10: owasp-top10.yml
  ISO-27001: iso-27001.yml
  PSD2: psd2.yml
  WCAG: wcag.yml
```

A standard listed in a selection group but **without** a resolvable file entry in
`standards:` is treated as *unavailable* — findings for it map as `unmapped`, never
guessed.

## Per-standard file schema

```yaml
id: GDPR
name: General Data Protection Regulation (EU) 2016/679
authority_url: https://eur-lex.europa.eu/eli/reg/2016/679/oj
catalog_version: "2026.07"
last_verified: "2026-07-03"
jurisdiction: EU
clauses:
  - id: "Art.5(1)(c)"
    title: Data minimisation
    applies_to: [pii_over_collection, pii_in_response]   # links to signal subtypes / concepts
    check_hint: "Only data necessary for the stated purpose is collected or returned."
```

- **`id`** — the official clause/article/requirement identifier, exactly as the
  authority writes it. This is the citeable token.
- **`applies_to`** — concept tags that let the skill map a confirmed code issue (or
  a `tornhill-scan-signals.py` candidate subtype like `pii_field`, `sql_concat`,
  `log_sensitive`) to the right clause.
- **`check_hint`** — a one-line reminder of what compliance looks like; guidance for
  the reviewer, not a verdict.

## Adding or refreshing a standard

1. Add `<id>: <file>.yml` under `standards:` in `index.yml` and wire it into the
   relevant `by_domain` / `by_region` / `by_condition` group.
2. Create the file with real clause IDs from the authority. **Never invent a clause
   number.** If unsure, leave it out — an incomplete catalog just yields more honest
   `unmapped` findings.
3. Bump `catalog_version` and set `last_verified` to today.
4. A future `refresh-standards` op will re-check each `authority_url` and restamp
   `last_verified`.

## Coverage note

The catalog ships a curated core (GDPR, CCPA, HIPAA, PCI-DSS, OWASP Top 10,
ISO 27001, PSD2, WCAG). Other regimes (UK-GDPR, LGPD, PIPL, NIST CSF, HL7-FHIR,
ISO 20022, EN 301 549) are added over time — until a regime has a file, its
findings are reported as `unmapped` rather than mapped to a guessed clause.
