"""Per-field schema validation.

Checks (US-only, per the demo guardrail):
  - email: must match a sane regex
  - phone: must be E.164 US (+1 + 10 digits)
  - state: must be in the 50 US codes
  - country: must equal "US" (when populated)
  - founded_year: must be <= current year (2026 in seed; recalculated from system)

Blanks are NOT flagged here — completeness_checker handles those. Schema
violations are *populated but malformed* values.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterator

import pandas as pd

from ..seed.generator import US_STATES
from ._issue import Issue

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_E164_US_RE = re.compile(r"^\+1\d{10}$")

_US_STATE_SET = set(US_STATES)
_CURRENT_YEAR = datetime.now(timezone.utc).year


def _populated(v) -> bool:
    if v is None:
        return False
    if isinstance(v, float) and pd.isna(v):
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def validate_accounts(accounts: pd.DataFrame) -> Iterator[Issue]:
    if accounts.empty:
        return
    for row in accounts.itertuples(index=False):
        rid = getattr(row, "id", "")

        # Phone
        phone = getattr(row, "phone", "")
        if _populated(phone) and not _E164_US_RE.match(str(phone)):
            yield Issue(
                record_id=rid,
                record_type="account",
                issue_type="schema_violation",
                severity="high",
                field="phone",
                detail={"value": str(phone), "expected": "+1XXXXXXXXXX (E.164 US)"},
            )

        # State
        state = getattr(row, "state", "")
        if _populated(state) and str(state).strip().upper() not in _US_STATE_SET:
            yield Issue(
                record_id=rid,
                record_type="account",
                issue_type="schema_violation",
                severity="medium",
                field="state",
                detail={"value": str(state), "expected": "valid US 2-letter state code"},
            )

        # Country
        country = getattr(row, "country", "")
        if _populated(country) and str(country).strip().upper() != "US":
            yield Issue(
                record_id=rid,
                record_type="account",
                issue_type="schema_violation",
                severity="low",
                field="country",
                detail={"value": str(country), "expected": "US (demo is US-only)"},
            )

        # Founded year
        fy = getattr(row, "founded_year", None)
        if _populated(fy):
            try:
                fy_int = int(float(fy))
                if fy_int > _CURRENT_YEAR:
                    yield Issue(
                        record_id=rid,
                        record_type="account",
                        issue_type="schema_violation",
                        severity="medium",
                        field="founded_year",
                        detail={"value": fy_int, "expected": f"<= {_CURRENT_YEAR}"},
                    )
            except (TypeError, ValueError):
                yield Issue(
                    record_id=rid,
                    record_type="account",
                    issue_type="schema_violation",
                    severity="medium",
                    field="founded_year",
                    detail={"value": str(fy), "expected": "integer year"},
                )


def validate_contacts(contacts: pd.DataFrame) -> Iterator[Issue]:
    if contacts.empty:
        return
    for row in contacts.itertuples(index=False):
        rid = getattr(row, "id", "")

        email = getattr(row, "email", "")
        if _populated(email) and not _EMAIL_RE.match(str(email).strip()):
            yield Issue(
                record_id=rid,
                record_type="contact",
                issue_type="schema_violation",
                severity="high",
                field="email",
                detail={"value": str(email), "expected": "valid RFC-ish email"},
            )

        phone = getattr(row, "phone", "")
        if _populated(phone) and not _E164_US_RE.match(str(phone)):
            yield Issue(
                record_id=rid,
                record_type="contact",
                issue_type="schema_violation",
                severity="medium",
                field="phone",
                detail={"value": str(phone), "expected": "+1XXXXXXXXXX (E.164 US)"},
            )
