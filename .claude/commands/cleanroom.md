---
description: Run the Cleanroom CRM hygiene pipeline on a pair of CSVs (audit + resolve + enrich + push + report)
argument-hint: <accounts.csv> <contacts.csv> [--commit] [--live]
---

You are running the Cleanroom CRM-hygiene pipeline against the files the user passed in `$ARGUMENTS`.

## Parse the arguments

Treat `$ARGUMENTS` as a space-separated list. Expect:
- positional 1: path to an accounts CSV
- positional 2: path to a contacts CSV
- optional: `--commit` (default off → push step is dry-run)
- optional: `--live`   (default off → use real Apollo + Claude APIs)

Validate the two CSVs exist on disk. Stop with a clear error if not.

## Run the pipeline

Run each stage with `Bash` from the repo root. Use the venv if `.venv/bin/python` exists; otherwise the user's python. Print stage timing as you go.

1. `python -m cleanroom audit <accounts.csv> <contacts.csv> --out data/audit/`
2. `python -m cleanroom resolve <accounts.csv> data/audit/issues.jsonl --out data/audit/{{LIVE_FLAG}}`
3. `python -m cleanroom enrich data/audit/accounts_resolved.csv --out data/enrichment/{{LIVE_FLAG}}`
4. `python -m cleanroom push data/enrichment/accounts_enriched.csv data/audit/merge_plan.json data/enrichment/field_metadata.jsonl --out data/push/{{COMMIT_FLAG}}`
5. Render the HTML report by calling `python scripts/run_demo.py --no-open` (it re-runs cheaply and writes `reports/before_after.html`).

`{{LIVE_FLAG}}` is empty in dry-run, or ` --live` when `--live` was passed. `{{COMMIT_FLAG}}` is empty in dry-run, or ` --commit` when `--commit` was passed.

## Tell the user what happened

Summarize at the end:
- N accounts before → N canonical after (-M merged)
- N dup pairs found (high + LLM-resolved breakdown)
- N fields enriched (source breakdown: apollo / mock_clearbit / claude_websearch)
- Report path: `reports/before_after.html`
- Push status: dry-run + manifest path, OR pushed + record count

## Guardrails

- Never run with `--commit` unless the user explicitly typed `--commit` in $ARGUMENTS — push to a live Salesforce org needs the user's explicit consent each time.
- If any stage exits non-zero, surface the error and stop. Don't proceed past a failed audit.
- If `--live` is set but ANTHROPIC_API_KEY or APOLLO_API_KEY is missing, warn the user and fall back to dry-run for that provider.
