# Cleanroom

> Every CRM I touched as a rep was 40% garbage. Acme Corp, ACME Corp, and Acme Corporation were three separate accounts. Industry was blank on a third of them. Half the contacts had bounced. My AE asked me to "verify the data before you log a touch," which meant 20 minutes of LinkedIn-tab whack-a-mole per record. Reps stop trusting the CRM, stop logging activity, and leadership stops trusting the dashboards.
>
> Cleanroom is a one-command audit + repair pipeline. Point it at a CSV dump (or a Salesforce dev org), and it deduplicates with fuzzy matching + an LLM tie-breaker for the gray zone, runs a schema and completeness audit, enriches missing fields via a Clay-style waterfall (Apollo → mocks → Claude), and pushes a clean dataset back — with a before/after HTML report a CRO can actually read.

**Loom demo:** *coming once the build runs end-to-end* — placeholder will be replaced with the embed.

---

## Quickstart

```bash
git clone https://github.com/<your-handle>/cleanroom
cd cleanroom
./scripts/setup.sh                 # venv + editable install + macOS .pth workaround
source .venv/bin/activate
python scripts/run_demo.py --size 1000
```

Runs the full five-stage pipeline on 1,000 synthetic accounts in under 60 seconds and auto-opens the before/after HTML report.

Real Salesforce push is opt-in:
```bash
python scripts/run_demo.py --size 1000 --commit   # requires .env with SF_* + ANTHROPIC + APOLLO keys
```

---

## What it does

- **Dedups** ~12% of accounts that exist as case/whitespace/legal-suffix/typo/domain variants of each other.
- **Validates** schema (email format, phone E.164, US state codes, country, founded-year sanity).
- **Fills gaps** — industry, revenue, employee count, country, phone — via a three-tier enrichment waterfall with per-field source + confidence + timestamp.
- **Resolves the gray zone** — `rapidfuzz` decides 95% of dedup pairs deterministically; the 70–90 ambiguous band goes to Claude Haiku 4.5 with structured output.
- **Pushes back to Salesforce** via Bulk API with four `cleanroom_*__c` custom fields tracking the audit metadata.
- **Renders a before/after HTML report** with the kinds of numbers a CRO reads in a Monday-morning standup.

---

## What this is not

- **Not a Salesforce or HubSpot managed package.** It's a Python pipeline you run from a terminal or a slash command.
- **Not a real-time deduper.** It's a batch audit + repair tool, run on demand or scheduled.
- **Not a Clay competitor.** Clay is way more configurable. Cleanroom is opinionated and runnable from one command.
- **Not magic.** The LLM tie-breaker only fires on ambiguous matches (rapidfuzz score 70–90), not on every record. Most matches are decided by deterministic rules.
- **Not internationally validated.** US-only address/state/phone normalization for the demo. International would be a follow-on.

---

## Architecture

*Diagram + module-by-module breakdown lands here in Phase 6.* See [`docs/architecture.md`](docs/architecture.md) once written, and [`CLAUDE.md`](CLAUDE.md) for the current build status.

---

## Credits

- Reference patterns from [gooseworks-ai/goose-skills](https://github.com/gooseworks-ai/goose-skills) — specifically `contact-cache` (dedup state shape), `apollo-lead-finder` (two-phase match → enrich), and `inbound-lead-enrichment` (provider fallback chain + per-field confidence). Inspiration only — not installed as a dependency.
- Waterfall enrichment **philosophy** lifted from Clay's public docs (multi-provider chains beat any single source).
- Sibling-repo conventions (macOS `chflags` workaround, `DRY_RUN=1` demo-safe default, slash-command-first design) from earlier projects in this portfolio.
