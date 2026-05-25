"""Audit module — non-destructive scan over accounts + contacts.

The top-level entry point is `run_audit()`. It runs every detector, writes
issues.jsonl + summary.json to the output directory, and returns an
AuditResult that downstream stages (resolve, enrich, report) can consume
without re-reading the JSONL.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ._issue import Issue
from .completeness_checker import (
    check_completeness_accounts,
    check_completeness_contacts,
    check_inactive_owners,
    fill_rate,
)
from .duplicate_detector import find_account_duplicates, find_contact_duplicates
from .orphan_finder import (
    find_lifecycle_inconsistencies,
    find_orphan_accounts,
    find_orphan_contacts,
)
from .schema_validator import validate_accounts, validate_contacts


@dataclass
class AuditResult:
    issues: list[Issue]
    account_fill_rates: dict[str, float]
    contact_fill_rates: dict[str, float]
    n_accounts: int
    n_contacts: int

    @property
    def counts_by_type(self) -> dict[str, int]:
        return dict(Counter(i.issue_type for i in self.issues))

    @property
    def counts_by_severity(self) -> dict[str, int]:
        return dict(Counter(i.severity for i in self.issues))

    @property
    def has_critical(self) -> bool:
        """True if any 'high' severity issue exists. Drives audit CLI exit code."""
        return any(i.severity == "high" for i in self.issues)

    def summary(self) -> dict:
        return {
            "n_accounts": self.n_accounts,
            "n_contacts": self.n_contacts,
            "total_issues": len(self.issues),
            "counts_by_type": self.counts_by_type,
            "counts_by_severity": self.counts_by_severity,
            "account_fill_rates": self.account_fill_rates,
            "contact_fill_rates": self.contact_fill_rates,
        }


def run_audit(accounts: pd.DataFrame, contacts: pd.DataFrame) -> AuditResult:
    """Run all detectors in a fixed order. Order doesn't matter for correctness
    but is fixed so issues.jsonl is reproducible."""
    issues: list[Issue] = []

    # 1. Duplicates (most expensive — rapidfuzz scoring)
    issues.extend(find_account_duplicates(accounts))
    issues.extend(find_contact_duplicates(contacts))

    # 2. Schema violations
    issues.extend(validate_accounts(accounts))
    issues.extend(validate_contacts(contacts))

    # 3. Completeness gaps
    issues.extend(check_completeness_accounts(accounts))
    issues.extend(check_completeness_contacts(contacts))

    # 4. Owner integrity
    issues.extend(check_inactive_owners(accounts, "account"))
    issues.extend(check_inactive_owners(contacts, "contact"))

    # 5. Cross-table orphans + lifecycle
    issues.extend(find_orphan_accounts(accounts, contacts))
    issues.extend(find_orphan_contacts(contacts, accounts))
    issues.extend(find_lifecycle_inconsistencies(accounts, contacts))

    return AuditResult(
        issues=issues,
        account_fill_rates=fill_rate(accounts),
        contact_fill_rates=fill_rate(contacts),
        n_accounts=len(accounts),
        n_contacts=len(contacts),
    )


def write_audit_outputs(result: AuditResult, out_dir: Path) -> None:
    """Write issues.jsonl + summary.json to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    issues_path = out_dir / "issues.jsonl"
    with issues_path.open("w") as f:
        for issue in result.issues:
            f.write(json.dumps(issue.to_dict()) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(result.summary(), indent=2, default=str))
