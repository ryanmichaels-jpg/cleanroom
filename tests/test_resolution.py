"""Resolution tests — LLM tie-break (dry-run) + merge_strategy."""

from __future__ import annotations

import pandas as pd

from cleanroom.audit._issue import Issue
from cleanroom.resolution import run_resolution
from cleanroom.resolution.llm_tiebreaker import (
    TieBreakDecision,
    _dry_run_decide,
)
from cleanroom.resolution.merge_strategy import build_merge_plan


def _acct(id_, name, domain, **kwargs):
    base = {
        "id": id_, "name": name, "domain": domain,
        "industry": "", "annual_revenue": None, "employee_count": None,
        "founded_year": 2000, "country": "US", "state": "CA", "city": "X",
        "phone": "+15551234567", "owner_id": "owner_001",
        "lifecycle_stage": "Lead", "created_at": "2023-01-01T00:00:00+00:00",
        "updated_at": "2023-01-01T00:00:00+00:00",
    }
    base.update(kwargs)
    return base


def test_dry_run_high_score_same_domain_decides_true():
    issue = Issue(
        record_id="acct_002",
        record_type="account",
        issue_type="duplicate_candidate",
        severity="medium",
        detail={"matched_with": "acct_001", "score": 88.0, "same_domain_root": True},
    )
    accts = {
        "acct_001": _acct("acct_001", "Acme Corp", "acme.com"),
        "acct_002": _acct("acct_002", "Acme Corporation", "acme.io"),
    }
    decision = _dry_run_decide(issue, accts)
    assert decision.same_entity is True
    assert decision.confidence == "high"


def test_dry_run_low_score_different_domain_decides_false():
    issue = Issue(
        record_id="acct_002",
        record_type="account",
        issue_type="duplicate_candidate",
        severity="medium",
        detail={"matched_with": "acct_001", "score": 72.0, "same_domain_root": False},
    )
    accts = {
        "acct_001": _acct("acct_001", "Some Co", "someco.com"),
        "acct_002": _acct("acct_002", "Sloan & Co", "sloan.io"),
    }
    decision = _dry_run_decide(issue, accts)
    assert decision.same_entity is False
    assert decision.confidence == "low"


def test_merge_collapses_high_severity_pairs_into_one_canonical():
    accounts = pd.DataFrame([
        _acct("acct_001", "Acme Corporation", "acme.com", industry="Manufacturing", annual_revenue=10_000_000),
        _acct("acct_002", "Acme Corp",        "acme.com", industry="",              annual_revenue=15_000_000),
        _acct("acct_003", "ACME CORP",        "acme.com", industry="Manufacturing", annual_revenue=None),
    ])
    issues = [
        Issue("acct_002", "account", "duplicate_candidate", "high",
              detail={"matched_with": "acct_001", "score": 100}),
        Issue("acct_003", "account", "duplicate_candidate", "high",
              detail={"matched_with": "acct_001", "score": 100}),
    ]
    merged, plan, canon_map = build_merge_plan(accounts, issues, [])
    # 3 → 1 account
    assert len(merged) == 1
    assert plan[0].canonical_id == "acct_001"
    assert set(plan[0].merged_ids) == {"acct_002", "acct_003"}
    # Industry filled (longest-non-blank), revenue → max
    canonical = merged.iloc[0]
    assert canonical["industry"] == "Manufacturing"
    assert canonical["annual_revenue"] == 15_000_000
    assert canon_map["acct_002"] == "acct_001"
    assert canon_map["acct_003"] == "acct_001"


def test_gray_zone_llm_true_extends_merge_group():
    accounts = pd.DataFrame([
        _acct("acct_001", "Acme Corp", "acme.com"),
        _acct("acct_002", "Acme Corporation", "getacme.com"),  # gray-zone with #001
    ])
    issues = [
        Issue("acct_002", "account", "duplicate_candidate", "medium",
              detail={"matched_with": "acct_001", "score": 82, "same_domain_root": False}),
    ]
    decisions = [
        TieBreakDecision(
            pair_record_id="acct_002",
            matched_with="acct_001",
            same_entity=True,
            confidence="medium",
            reasoning="same logical company",
            source="dry-run-heuristic",
        ),
    ]
    merged, plan, _ = build_merge_plan(accounts, issues, decisions)
    assert len(merged) == 1
    assert plan[0].confidence == "medium"  # gray-zone-influenced
    assert "acct_002" in plan[0].merged_ids


def test_run_resolution_on_messy_dataset(monkeypatch):
    """Integration: 5 accounts, 2 mergeable pairs, dry-run end-to-end."""
    accounts = pd.DataFrame([
        _acct("acct_001", "Acme Corporation", "acme.com"),
        _acct("acct_002", "Acme Corp", "acme.com"),
        _acct("acct_003", "ACME CORP", "acme.com"),
        _acct("acct_004", "Globex", "globex.com"),
        _acct("acct_005", "Initech", "initech.com"),
    ])
    issues = [
        Issue("acct_002", "account", "duplicate_candidate", "high",
              detail={"matched_with": "acct_001", "score": 100, "same_domain_root": True}),
        Issue("acct_003", "account", "duplicate_candidate", "high",
              detail={"matched_with": "acct_001", "score": 100, "same_domain_root": True}),
    ]
    resolved, plan, decisions, telemetry, _ = run_resolution(accounts, issues, live=False)
    assert len(resolved) == 3  # acct_001 + acct_004 + acct_005
    assert telemetry["mode"] == "dry-run"
    assert telemetry["n_merge_groups"] == 1
    assert telemetry["n_records_merged_away"] == 2
