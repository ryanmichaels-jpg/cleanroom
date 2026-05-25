"""Apply confirmed duplicate pairs to the accounts dataframe.

Inputs:
  - high-severity duplicate_candidate issues (auto-merge, rapidfuzz >=90)
  - TieBreakDecision rows where same_entity=True (LLM-confirmed gray-zone merges)

Process:
  1. Build a union-find across all confirmed pairs.
  2. For each connected component, pick a canonical record:
       - lowest record_id (stable across runs).
  3. For each non-canonical in the component, set its `cleanroom_dedup_canonical_id__c` field.
  4. Merge field values into the canonical row using field-type rules:
       - text   → longest non-blank value wins
       - numeric → max non-null wins (treats missing as smaller)
       - timestamps → max (most-recent) wins
       - lifecycle_stage → most-advanced stage wins (Lead < MQL < SQL < Opportunity < Customer)
  5. Mark non-canonical rows with `_merged_into` so the push stage knows to skip them.

Output: (merged_accounts_df, merge_plan)
  merge_plan: list of {canonical_id, merged_ids[], reasoning, confidence}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from ..audit._issue import Issue
from .llm_tiebreaker import TieBreakDecision


_LIFECYCLE_RANK = {
    "Lead": 1,
    "MQL": 2,
    "SQL": 3,
    "Opportunity": 4,
    "Customer": 5,
    "Closed Lost": 0,
    "": -1,
}


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self.parent:
            self.parent[x] = x
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        # Smaller id wins (lex order on stable IDs is fine here).
        if ra <= rb:
            self.parent[rb] = ra
        else:
            self.parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for node in self.parent:
            root = self.find(node)
            out.setdefault(root, []).append(node)
        return out


@dataclass
class MergePlanEntry:
    canonical_id: str
    merged_ids: list[str]
    confidence: str
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "canonical_id": self.canonical_id,
            "merged_ids": self.merged_ids,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


def _best_text(values: list) -> str:
    """Longest non-blank string wins (more info beats less)."""
    best = ""
    for v in values:
        s = "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
        if s and len(s) > len(best):
            best = s
    return best


def _best_numeric(values: list) -> float | None:
    """Max non-null wins. (NaNs treated as missing.)"""
    nums = []
    for v in values:
        if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
            continue
        try:
            nums.append(float(v))
        except (TypeError, ValueError):
            continue
    return max(nums) if nums else None


def _best_lifecycle(values: list) -> str:
    """Most-advanced stage wins."""
    best = ""
    best_rank = -2
    for v in values:
        s = "" if v is None else str(v).strip()
        rank = _LIFECYCLE_RANK.get(s, -1)
        if rank > best_rank:
            best, best_rank = s, rank
    return best


_TEXT_FIELDS = {"name", "domain", "industry", "country", "state", "city", "phone", "owner_id"}
_NUMERIC_FIELDS = {"annual_revenue", "employee_count", "founded_year"}
_TIMESTAMP_FIELDS = {"created_at", "updated_at"}


def _merge_group(rows: pd.DataFrame, canonical_id: str) -> pd.Series:
    """Collapse a group of rows into one canonical row using per-field rules."""
    merged: dict = {"id": canonical_id}
    for col in rows.columns:
        if col == "id":
            continue
        values = rows[col].tolist()
        if col in _TEXT_FIELDS:
            merged[col] = _best_text(values)
        elif col in _NUMERIC_FIELDS:
            merged[col] = _best_numeric(values)
        elif col in _TIMESTAMP_FIELDS:
            non_blank = [v for v in values if v and (not isinstance(v, float) or not pd.isna(v))]
            merged[col] = max(non_blank) if non_blank else ""
        elif col == "lifecycle_stage":
            merged[col] = _best_lifecycle(values)
        else:
            # default: longest non-blank
            merged[col] = _best_text(values)
    return pd.Series(merged)


def build_merge_plan(
    accounts: pd.DataFrame,
    duplicate_issues: Iterable[Issue],
    tiebreak_decisions: Iterable[TieBreakDecision],
) -> tuple[pd.DataFrame, list[MergePlanEntry], dict[str, str]]:
    """Apply merges. Returns (merged_accounts_df, merge_plan, dedup_canonical_map).

    dedup_canonical_map: {original_id: canonical_id} for the push stage to
    write `cleanroom_dedup_canonical_id__c` back to Salesforce.
    """
    uf = _UnionFind()

    # High-severity dupes → auto-merge
    high_reasons: dict[tuple[str, str], str] = {}
    for issue in duplicate_issues:
        if issue.issue_type != "duplicate_candidate" or issue.record_type != "account":
            continue
        if issue.severity == "high":
            a = issue.detail.get("matched_with", "")
            b = issue.record_id
            if a and b:
                uf.union(a, b)
                high_reasons[(min(a, b), max(a, b))] = (
                    f"rapidfuzz score {issue.detail.get('score', 0)} >= 90"
                )

    # Gray-zone decisions where same_entity=True → merge
    gray_reasons: dict[tuple[str, str], TieBreakDecision] = {}
    for dec in tiebreak_decisions:
        if dec.same_entity:
            uf.union(dec.matched_with, dec.pair_record_id)
            key = (min(dec.matched_with, dec.pair_record_id), max(dec.matched_with, dec.pair_record_id))
            gray_reasons[key] = dec

    groups = uf.groups()
    canonical_map: dict[str, str] = {}
    plan: list[MergePlanEntry] = []

    by_id = {row["id"]: row.to_dict() for _, row in accounts.iterrows()}

    keep_ids = set(accounts["id"].astype(str).tolist())
    merged_rows: list[pd.Series] = []

    for canonical, members in groups.items():
        if len(members) < 2:
            continue
        # Confidence: if any gray_zone pair is in this group → "medium", else "high"
        has_gray = any(
            (min(canonical, m), max(canonical, m)) in gray_reasons
            for m in members if m != canonical
        )
        reasoning_bits = []
        for m in members:
            if m == canonical:
                continue
            key = (min(canonical, m), max(canonical, m))
            if key in high_reasons:
                reasoning_bits.append(f"{m}: {high_reasons[key]}")
            elif key in gray_reasons:
                d = gray_reasons[key]
                reasoning_bits.append(f"{m}: LLM said same_entity (conf={d.confidence}) — {d.reasoning}")
        confidence = "medium" if has_gray else "high"

        plan.append(MergePlanEntry(
            canonical_id=canonical,
            merged_ids=[m for m in members if m != canonical],
            confidence=confidence,
            reasoning="; ".join(reasoning_bits) or "rapidfuzz auto-merge",
        ))

        member_rows = pd.DataFrame([by_id[m] for m in members if m in by_id])
        merged_rows.append(_merge_group(member_rows, canonical))

        for m in members:
            canonical_map[m] = canonical
            if m != canonical:
                keep_ids.discard(m)

    # Build the merged accounts DataFrame: keep unmerged + replace canonical rows with merged ones
    new_accounts = accounts[accounts["id"].astype(str).isin(keep_ids)].copy()
    if merged_rows:
        merged_df = pd.DataFrame(merged_rows)
        # Drop the canonical rows from new_accounts (they get replaced by the merged versions)
        canonical_ids = {m.canonical_id for m in plan}
        new_accounts = new_accounts[~new_accounts["id"].astype(str).isin(canonical_ids)]
        new_accounts = pd.concat([new_accounts, merged_df], ignore_index=True)

    # Stable order by id
    new_accounts = new_accounts.sort_values("id").reset_index(drop=True)
    return new_accounts, plan, canonical_map
