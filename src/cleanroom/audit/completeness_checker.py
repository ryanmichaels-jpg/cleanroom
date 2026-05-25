"""Per-field fill-rate and per-record completeness gaps.

Two outputs:
  - fill_rate(): aggregate stats (% populated per field) — used by the report.
  - check_completeness(): per-record Issue stream for fields that are blank.

Plus an "inactive_owner" check, since owner integrity is a lifecycle concern
that lives next to completeness in practice.
"""

from __future__ import annotations

from typing import Iterator

import pandas as pd

from ._issue import Issue

# Sales-critical fields whose blanks count as MEDIUM (rep can't act without them).
# Other blanks are LOW.
_HIGH_VALUE_ACCOUNT_FIELDS = {"industry", "country", "employee_count", "annual_revenue"}
_HIGH_VALUE_CONTACT_FIELDS = {"email", "title"}


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def fill_rate(df: pd.DataFrame, fields: list[str] | None = None) -> dict[str, float]:
    """Fraction of rows where the field is populated. Used by the HTML report."""
    if df.empty:
        return {}
    fields = fields or list(df.columns)
    out: dict[str, float] = {}
    for f in fields:
        if f not in df.columns:
            continue
        n = len(df)
        n_populated = sum(1 for v in df[f].tolist() if not _is_blank(v))
        out[f] = n_populated / n if n else 0.0
    return out


def check_completeness_accounts(accounts: pd.DataFrame) -> Iterator[Issue]:
    if accounts.empty:
        return
    fields_to_check = ["industry", "annual_revenue", "employee_count", "country", "phone", "state"]
    for row in accounts.itertuples(index=False):
        rid = getattr(row, "id", "")
        for f in fields_to_check:
            v = getattr(row, f, None)
            if _is_blank(v):
                sev = "medium" if f in _HIGH_VALUE_ACCOUNT_FIELDS else "low"
                yield Issue(
                    record_id=rid,
                    record_type="account",
                    issue_type="completeness_gap",
                    severity=sev,
                    field=f,
                    detail={"reason": "blank"},
                )


def check_completeness_contacts(contacts: pd.DataFrame) -> Iterator[Issue]:
    if contacts.empty:
        return
    fields_to_check = ["email", "phone", "title", "first_name", "last_name"]
    for row in contacts.itertuples(index=False):
        rid = getattr(row, "id", "")
        for f in fields_to_check:
            v = getattr(row, f, None)
            if _is_blank(v):
                sev = "medium" if f in _HIGH_VALUE_CONTACT_FIELDS else "low"
                yield Issue(
                    record_id=rid,
                    record_type="contact",
                    issue_type="completeness_gap",
                    severity=sev,
                    field=f,
                    detail={"reason": "blank"},
                )


def check_inactive_owners(df: pd.DataFrame, record_type: str) -> Iterator[Issue]:
    """Records owned by users matching 'inactive_user_*' need re-assignment."""
    if df.empty or "owner_id" not in df.columns:
        return
    for row in df.itertuples(index=False):
        rid = getattr(row, "id", "")
        owner = str(getattr(row, "owner_id", "") or "")
        if owner.startswith("inactive_user_"):
            yield Issue(
                record_id=rid,
                record_type=record_type,  # type: ignore[arg-type]
                issue_type="inactive_owner",
                severity="medium",
                field="owner_id",
                detail={"owner_id": owner, "reason": "owner marked inactive"},
            )
