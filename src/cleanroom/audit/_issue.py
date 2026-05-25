"""Shared issue-record schema for everything the audit emits.

Output contract (one row per issue in data/audit/issues.jsonl):

    {
      "record_id":   "acct_000123",
      "record_type": "account" | "contact",
      "issue_type":  "duplicate_candidate" | "schema_violation" | ...,
      "severity":    "high" | "medium" | "low",
      "field":       "phone" | null,        # the affected field, if scoped
      "detail":      { ... }                # issue-type-specific payload
    }

Resolution + enrichment downstream read this same JSONL — keep the keys stable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
from typing import Any, Literal

Severity = Literal["high", "medium", "low"]
RecordType = Literal["account", "contact"]


@dataclass
class Issue:
    record_id: str
    record_type: RecordType
    issue_type: str
    severity: Severity
    field: str | None = None
    detail: dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
