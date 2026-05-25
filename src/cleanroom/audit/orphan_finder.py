"""Cross-table integrity: orphan accounts, orphan contacts, lifecycle mismatches."""

from __future__ import annotations

from typing import Iterator

import pandas as pd

from ._issue import Issue


def _is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and not v.strip():
        return True
    return False


def find_orphan_accounts(accounts: pd.DataFrame, contacts: pd.DataFrame) -> Iterator[Issue]:
    """Account exists but has zero contacts pointing at it."""
    if accounts.empty:
        return
    if contacts.empty:
        # everything's an orphan
        for row in accounts.itertuples(index=False):
            yield Issue(
                record_id=getattr(row, "id", ""),
                record_type="account",
                issue_type="orphan_account",
                severity="low",
                field=None,
                detail={"reason": "no contacts table or empty"},
            )
        return

    linked = set(contacts["account_id"].astype(str).tolist())
    linked.discard("")
    for row in accounts.itertuples(index=False):
        rid = str(getattr(row, "id", ""))
        if rid and rid not in linked:
            yield Issue(
                record_id=rid,
                record_type="account",
                issue_type="orphan_account",
                severity="low",
                field=None,
                detail={"reason": "no contacts linked"},
            )


def find_orphan_contacts(contacts: pd.DataFrame, accounts: pd.DataFrame) -> Iterator[Issue]:
    """Contact has null/blank account_id, OR points at an account_id that
    doesn't exist in the accounts table."""
    if contacts.empty:
        return
    known_account_ids = set(accounts["id"].astype(str).tolist()) if not accounts.empty else set()
    for row in contacts.itertuples(index=False):
        rid = str(getattr(row, "id", ""))
        acct = getattr(row, "account_id", "")
        if _is_blank(acct):
            yield Issue(
                record_id=rid,
                record_type="contact",
                issue_type="orphan_contact",
                severity="high",
                field="account_id",
                detail={"reason": "null account_id"},
            )
        elif str(acct) not in known_account_ids:
            yield Issue(
                record_id=rid,
                record_type="contact",
                issue_type="orphan_contact",
                severity="medium",
                field="account_id",
                detail={"reason": "account_id points to non-existent account", "value": str(acct)},
            )


def find_lifecycle_inconsistencies(
    accounts: pd.DataFrame, contacts: pd.DataFrame
) -> Iterator[Issue]:
    """Accounts marked 'Customer' but with zero contacts — common Salesforce
    data-quality fingerprint of stale closed-won records."""
    if accounts.empty:
        return
    linked = set(contacts["account_id"].astype(str).tolist()) if not contacts.empty else set()
    linked.discard("")
    for row in accounts.itertuples(index=False):
        rid = str(getattr(row, "id", ""))
        stage = str(getattr(row, "lifecycle_stage", "") or "")
        if stage == "Customer" and rid not in linked:
            yield Issue(
                record_id=rid,
                record_type="account",
                issue_type="lifecycle_inconsistency",
                severity="medium",
                field="lifecycle_stage",
                detail={"reason": "Customer stage but no contacts", "stage": stage},
            )
