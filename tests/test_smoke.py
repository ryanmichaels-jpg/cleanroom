"""Full-pipeline smoke test: 100-record seed through every stage.

This is the anchor test the kickoff requires. If it passes, the demo runs.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

from cleanroom.audit import run_audit, write_audit_outputs
from cleanroom.enrichment import run_enrichment
from cleanroom.push.salesforce_upsert import push_accounts
from cleanroom.report.html_renderer import ReportInputs, render_report
from cleanroom.resolution import run_resolution
from cleanroom.seed.flaw_injector import inject_all_flaws
from cleanroom.seed.generator import (
    GenContext,
    generate_clean_accounts,
    generate_clean_contacts,
)


CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "cleanroom" / "seed" / "seed_config.yaml"
)


def _build_seed(n_accounts: int, seed: int):
    config = yaml.safe_load(CONFIG_PATH.read_text())
    ctx = GenContext.from_seed(seed)
    rng = random.Random(seed + 1)
    accounts = generate_clean_accounts(n_accounts, ctx, hero_dupes=config.get("hero_dupes"))
    contacts = generate_clean_contacts(
        accounts, ctx,
        mean_per_account=config["dataset"]["default_contacts_per_account_mean"],
        max_per_account=config["dataset"]["default_contacts_per_account_max"],
    )
    accounts, contacts = inject_all_flaws(accounts, contacts, config, rng)
    return accounts, contacts


def test_full_pipeline_on_100_records(tmp_path: Path):
    """Run every stage end-to-end. Must produce a non-empty HTML report
    with the hero dupes resolved."""
    accounts, contacts = _build_seed(n_accounts=100, seed=42)

    # Stage 1: audit
    audit_before = run_audit(accounts, contacts)
    write_audit_outputs(audit_before, tmp_path / "audit")
    assert audit_before.has_critical
    assert (tmp_path / "audit" / "issues.jsonl").exists()
    assert audit_before.counts_by_type.get("duplicate_candidate", 0) > 0

    # Stage 2: resolve
    resolved, plan, decisions, telemetry, canonical_map = run_resolution(
        accounts, audit_before.issues, live=False
    )
    assert len(resolved) < len(accounts)  # at least one merge
    # Acme group should always exist on the hero-seeded dataset
    acme_canonical = [p for p in plan if p.canonical_id == "acct_000001"]
    assert len(acme_canonical) == 1
    assert len(acme_canonical[0].merged_ids) >= 3  # at least Acme Corp + ACME CORP + Acme, Inc.

    # Stage 3: enrich
    enriched, tracker = run_enrichment(resolved, live=False, cache_path=tmp_path / "cache.json")
    assert len(tracker) > 0, "expected at least some enrichment fills"
    assert "mock_clearbit" in tracker.by_source()

    # Stage 4: re-audit
    audit_after = run_audit(enriched, contacts)
    assert audit_after.counts_by_severity.get("high", 0) < audit_before.counts_by_severity["high"]

    # Stage 5: push (dry-run)
    push_result = push_accounts(enriched, canonical_map, tracker, tmp_path / "push", commit=False)
    assert push_result.mode == "dry_run"
    assert push_result.n_records_planned > 0
    manifest = tmp_path / "push" / "push_manifest.jsonl"
    assert manifest.exists()
    # First push record should have the four cleanroom_*__c fields
    first_record = json.loads(manifest.read_text().splitlines()[0])
    assert "cleanroom_audit_date__c" in first_record
    assert "cleanroom_dedup_canonical_id__c" in first_record
    assert "cleanroom_enrichment_sources__c" in first_record
    assert "cleanroom_confidence_score__c" in first_record

    # Stage 6: report
    inputs = ReportInputs(
        seed_csv_path="memory",
        mode="dry-run",
        audit_before=audit_before,
        audit_after=audit_after,
        n_accounts_before=len(accounts),
        n_accounts_after=len(resolved),
        n_contacts_before=len(contacts),
        n_llm_resolved=telemetry["n_same_entity_true"],
        merge_plan=plan,
        tracker=tracker,
    )
    report_path = tmp_path / "report.html"
    render_report(inputs, accounts, report_path, live=False, open_browser=False)
    assert report_path.exists()
    html = report_path.read_text()
    assert "Acme Corporation" in html  # hero dupe must surface
    assert "Cleanroom" in html
    # Confidence sources must be named
    assert "mock_clearbit" in html
