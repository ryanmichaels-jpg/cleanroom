"""Resolution: take the audit's duplicate_candidate issues, run an LLM
tie-breaker on the gray zone, and apply merges to the accounts dataframe.

Outputs (written by the CLI):
  - data/audit/merge_plan.json
  - data/audit/decisions_log.jsonl (one row per tie-break decision)
  - data/audit/accounts_resolved.csv (post-merge accounts)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pandas as pd

from ..audit._issue import Issue
from .llm_tiebreaker import TieBreakDecision, resolve_gray_zone
from .merge_strategy import MergePlanEntry, build_merge_plan


def run_resolution(
    accounts: pd.DataFrame,
    issues: list[Issue],
    *,
    live: bool = False,
    cap: int | None = None,
) -> tuple[pd.DataFrame, list[MergePlanEntry], list[TieBreakDecision], dict, dict[str, str]]:
    """Resolve dupes. Returns:
        (resolved_accounts_df, merge_plan, tiebreak_decisions, telemetry, canonical_map)
    """
    accounts_by_id = {str(r["id"]): r.to_dict() for _, r in accounts.iterrows()}
    decisions, telemetry = resolve_gray_zone(
        issues, accounts_by_id, live=live, cap=cap
    )
    resolved, plan, canonical_map = build_merge_plan(accounts, issues, decisions)
    telemetry["n_merge_groups"] = len(plan)
    telemetry["n_records_merged_away"] = sum(len(p.merged_ids) for p in plan)
    return resolved, plan, decisions, telemetry, canonical_map


def write_resolution_outputs(
    out_dir: Path,
    resolved_accounts: pd.DataFrame,
    plan: list[MergePlanEntry],
    decisions: list[TieBreakDecision],
    telemetry: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    resolved_accounts.to_csv(out_dir / "accounts_resolved.csv", index=False)
    (out_dir / "merge_plan.json").write_text(
        json.dumps({"telemetry": telemetry, "plan": [p.to_dict() for p in plan]}, indent=2)
    )
    with (out_dir / "decisions_log.jsonl").open("w") as f:
        for d in decisions:
            f.write(json.dumps(asdict(d)) + "\n")
