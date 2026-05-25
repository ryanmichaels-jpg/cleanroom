"""Salesforce dev-org write-back via simple-salesforce Bulk API.

Dry-run default (DRY_RUN=1 in .env): logs what would have been pushed,
writes a manifest, never opens an SF session. Demo-safe.

Commit mode (--commit + creds in .env): opens an SF session and Bulk-upserts
Accounts using `External_Id__c` as the external ID. Sets four custom fields
on every pushed Account:
  - cleanroom_audit_date__c          (ISO date)
  - cleanroom_confidence_score__c    (0.0-1.0, avg of per-field confidence)
  - cleanroom_dedup_canonical_id__c  (canonical id this row was merged into)
  - cleanroom_enrichment_sources__c  (pipe-delimited sources)

scripts/setup_sf_schema.py creates these custom fields via the Metadata API.
Document this in docs/demo.md.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd

from ..enrichment.confidence_tracker import ConfidenceTracker


# Mapping: our column → Salesforce Account field
_SF_FIELD_MAP = {
    "id":              "External_Id__c",
    "name":            "Name",
    "domain":          "Website",
    "industry":        "Industry",
    "annual_revenue":  "AnnualRevenue",
    "employee_count":  "NumberOfEmployees",
    "phone":           "Phone",
    "country":         "BillingCountry",
    "state":           "BillingState",
    "city":            "BillingCity",
}


@dataclass
class PushResult:
    mode: str                     # "dry_run" | "commit"
    n_records_planned: int
    n_records_pushed: int
    n_errors: int
    error_samples: list[dict] = field(default_factory=list)
    manifest_path: str = ""


def _build_record(row: pd.Series, canonical_map: dict[str, str], tracker: ConfidenceTracker) -> dict:
    """Translate one cleanroom account row to a Salesforce-shaped dict."""
    rec: dict = {}
    for our_col, sf_field in _SF_FIELD_MAP.items():
        if our_col not in row.index:
            continue
        v = row[our_col]
        if v is None or (isinstance(v, float) and pd.isna(v)):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        rec[sf_field] = v if not isinstance(v, str) else v.strip()

    rid = str(row.get("id", ""))
    rec["cleanroom_audit_date__c"] = date.today().isoformat()
    rec["cleanroom_confidence_score__c"] = tracker.confidence_score_for(rid)
    rec["cleanroom_dedup_canonical_id__c"] = canonical_map.get(rid, rid)
    rec["cleanroom_enrichment_sources__c"] = tracker.cleanroom_enrichment_sources_for(rid)
    return rec


def push_accounts(
    accounts: pd.DataFrame,
    canonical_map: dict[str, str],
    tracker: ConfidenceTracker,
    out_dir: Path,
    *,
    commit: bool = False,
) -> PushResult:
    """Push accounts to Salesforce, or just log the manifest in dry-run."""
    out_dir.mkdir(parents=True, exist_ok=True)

    # Filter to canonical rows only — non-canonicals were merged away.
    canonicals = set(canonical_map.values()) | {
        rid for rid in accounts["id"].astype(str) if rid not in canonical_map
    }
    push_df = accounts[accounts["id"].astype(str).isin(canonicals)].copy()

    records = [_build_record(row, canonical_map, tracker) for _, row in push_df.iterrows()]
    manifest_path = out_dir / "push_manifest.jsonl"
    with manifest_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")

    if not commit or os.getenv("DRY_RUN", "1") == "1":
        return PushResult(
            mode="dry_run",
            n_records_planned=len(records),
            n_records_pushed=0,
            n_errors=0,
            manifest_path=str(manifest_path),
        )

    # Real push path
    try:
        from simple_salesforce import Salesforce
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("simple-salesforce not installed; pip install -e .") from e

    username = os.getenv("SF_USERNAME") or ""
    password = os.getenv("SF_PASSWORD") or ""
    token = os.getenv("SF_SECURITY_TOKEN") or ""
    domain = os.getenv("SF_DOMAIN", "login")
    if not (username and password and token):
        raise RuntimeError("SF_USERNAME / SF_PASSWORD / SF_SECURITY_TOKEN must be set for --commit")

    sf = Salesforce(username=username, password=password, security_token=token, domain=domain)
    result = sf.bulk.Account.upsert(records, "External_Id__c")

    n_pushed = sum(1 for r in result if r.get("success"))
    errors = [r for r in result if not r.get("success")]

    return PushResult(
        mode="commit",
        n_records_planned=len(records),
        n_records_pushed=n_pushed,
        n_errors=len(errors),
        error_samples=errors[:5],
        manifest_path=str(manifest_path),
    )
