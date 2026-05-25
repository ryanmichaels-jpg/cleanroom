# CLAUDE.md — Cleanroom

Context for any Claude Code session working in this repo. Read this first.

---

## What this is

**Cleanroom** — CRM data quality + AI-assisted enrichment audit. Project 3 of 5 in the GTM Engineer portfolio. Lives in the parent `GTME portfolio/` folder; see `../CLAUDE.md` for the master portfolio brief and `../cleanroom-project-map.md` for the full architectural plan.

**The pitch (rep voice):** "Every CRM I touched as a rep was 40% garbage — dupes, blank industry fields, dead contacts, orphan accounts. Reps stop trusting data → stop using it → leadership stops trusting dashboards."

**The flow:**
```
seed (Faker, deterministic flaws)
  → audit (rapidfuzz dedup + schema + completeness + orphans)
  → resolve (LLM tie-break for the 70–90 gray zone, Haiku 4.5)
  → enrich (Apollo → mocks → Claude websearch; per-field source/conf/timestamp)
  → push (Salesforce Bulk API upsert with cleanroom_*__c custom fields)
  → report (auto-opening HTML before/after, jinja2 + inline chart.js)
```

---

## Stack

- **Matching:** `rapidfuzz` blocking + scored pairs. LLM only fires in the gray zone (rapidfuzz score 70–90), capped at 500 calls/run.
- **LLM:** Anthropic Claude — **Haiku 4.5** for dedup tie-break, **Sonnet 4.6** for the report summary narrative.
- **Enrichment:** Apollo `/v1/organizations/enrich` (real, free tier) → `mock_clearbit` (synthetic latency/match-rate) → `claude_websearch` (LLM-of-last-resort). Cached to `data/enrichment/cache.json`.
- **CRM target:** Salesforce dev org via `simple-salesforce` Bulk API. Dry-run default; `--commit` flag to actually push.
- **Custom fields written on Account:** `cleanroom_audit_date__c`, `cleanroom_confidence_score__c`, `cleanroom_dedup_canonical_id__c`, `cleanroom_enrichment_sources__c`.
- **Report:** standalone HTML (jinja2 + chart.js inline). No web server, no external requests at render time.
- **Seed:** Faker. Default demo size = 1,000 accounts / 3,000 contacts (runs <60s). Benchmark seed committed to `data/seed/` at 5,000 / 15,000.

---

## Repo conventions

- Synthetic test data in `data/seed/` is committed. Audit logs, enrichment cache, and pushed-records logs are gitignored (per-run).
- All credentials in `.env` (never committed). `.env.example` shows the shape.
- `DRY_RUN=1` in `.env` is the demo-safe default: pipeline runs all stages, but the Salesforce Bulk write is logged instead of sent.
- Slash command at `.claude/commands/cleanroom.md` is the user-facing API. Edit that *first* when changing the pipeline shape.
- One smoke test minimum: `tests/test_smoke.py` runs the full pipeline on 100 records.
- macOS gotcha replicated from sibling repos: `scripts/setup.sh` runs `chflags -R nohidden .venv` after `pip install -e .` because `.pth` files inherit `UF_HIDDEN` under `Documents/Claude/`.

---

## What we borrow (credit explicitly in README)

| Pattern | Source | Where it lands |
|---|---|---|
| CSV-backed dedup state | gooseworks-ai/goose-skills → `contact-cache` | `src/cleanroom/resolution/` |
| Two-phase enrichment (match → enrich) | gooseworks-ai/goose-skills → `apollo-lead-finder` | `src/cleanroom/enrichment/providers/apollo.py` |
| Provider fallback chain + per-field confidence | gooseworks-ai/goose-skills → `inbound-lead-enrichment` | `src/cleanroom/enrichment/waterfall.py` + `confidence_tracker.py` |
| Multi-provider waterfall philosophy | Clay docs (philosophy, not API) | README credit |
| SQLite-in-git data layer | Shawn Logan's Nexus Intel | *not used here* — Cleanroom uses CSV + JSONL. Credit only if pattern shows up. |

**Note:** the project map credits `tam-builder` for per-field confidence; on closer reading that skill uses aggregate weighted scoring (employees + industry + funding + geo + keywords → tier). The pattern we actually borrow is from `inbound-lead-enrichment` (field-level `high|medium|low` + `sources_used`). Crediting accurately.

---

## Don't

- Don't add Streamlit, FastAPI, React, or any web server. The report is **standalone HTML**.
- Don't pull in scikit-learn, transformers, or any ML framework. `rapidfuzz` + Claude is the entire matching stack.
- Don't try to handle international address normalization perfectly. US-only for the demo; note as a limitation in "what this is not."
- Don't install gooseworks-ai goose-skills as a dependency. Architectural inspiration only. Credit explicitly in README.
- Don't bring Clay's API in here as a live dependency — Project 1 (Reply Guy) replaced Clay's $500/mo tier with Apollo + an LLM agent for ~$5/mo; same logic applies here. Clay is credited for the *waterfall philosophy*, not as a runtime.
- Don't commit real credentials. Use the Salesforce dev org, Apollo free tier.
- Don't ship without the Loom link in README.

---

## Quick orient for a new session

1. Read this file.
2. Read `../cleanroom-project-map.md` for the full plan + open decisions.
3. Read `README.md` for the public framing.
4. Read `.claude/commands/cleanroom.md` for the user-facing flow shape.
5. `src/cleanroom/` is the package. `scripts/run_demo.py` is the entrypoint a recruiter sees.

---

## Build order (where we are)

Phase 1 — scaffold ✅
Phase 2 — seed module
Phase 3 — audit module
Phase 4 — resolution + enrichment
Phase 5 — Salesforce push + HTML report + demo runner
Phase 6 — slash command, README polish, verification
