"""Waterfall orchestrator: walk providers in order, take the first non-blank
hit for each blank field.

Provider order matters: Apollo (high conf, narrow coverage) → mock_clearbit
(med conf, broad coverage) → claude_websearch (low conf, fallback).

Inputs:
  - accounts DataFrame (already merged from the resolution stage)
  - list of providers (each implementing the Provider protocol)

Outputs:
  - enriched DataFrame (same shape, blanks filled where possible)
  - ConfidenceTracker (per-field source/confidence/timestamp)
"""

from __future__ import annotations

from typing import Iterable

import pandas as pd

from .confidence_tracker import ConfidenceTracker
from .providers._base import Provider


# Fields we attempt to fill via the waterfall. Phone is included because the
# enrichment providers may return it; but most blank phones stay blank.
_ENRICHABLE_FIELDS = ("industry", "annual_revenue", "employee_count", "country", "phone")


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def run_waterfall(
    accounts: pd.DataFrame,
    providers: Iterable[Provider],
) -> tuple[pd.DataFrame, ConfidenceTracker]:
    providers = list(providers)
    tracker = ConfidenceTracker()
    enriched = accounts.copy()

    # For numeric fields, accept Python numerics
    for col in ("annual_revenue", "employee_count"):
        if col in enriched.columns:
            enriched[col] = enriched[col].astype("object")

    for i, row in enriched.iterrows():
        record = row.to_dict()
        blanks = [f for f in _ENRICHABLE_FIELDS if f in record and _is_blank(record[f])]
        if not blanks:
            continue

        for provider in providers:
            if not blanks:
                break
            try:
                results = provider.enrich(record)
            except Exception:
                results = {}
            if not results:
                continue
            still_blank = []
            for f in blanks:
                if f in results:
                    res = results[f]
                    enriched.at[i, f] = res.value
                    record[f] = res.value
                    tracker.record(
                        record_id=str(record.get("id", "")),
                        field=f,
                        value=res.value,
                        source=res.source,
                        confidence=res.confidence,
                    )
                else:
                    still_blank.append(f)
            blanks = still_blank

    return enriched, tracker
