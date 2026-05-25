"""Audit module tests — detector-by-detector smoke + an end-to-end run."""

from __future__ import annotations

import pandas as pd
import pytest

from cleanroom.audit import run_audit
from cleanroom.audit.duplicate_detector import (
    domain_root,
    find_account_duplicates,
    find_contact_duplicates,
    normalize_name,
)
from cleanroom.audit.schema_validator import validate_accounts, validate_contacts
from cleanroom.audit.completeness_checker import check_completeness_accounts, fill_rate
from cleanroom.audit.orphan_finder import find_orphan_accounts, find_orphan_contacts


# --- normalization -----------------------------------------------------------


def test_normalize_name_strips_suffixes_and_case():
    assert normalize_name("Acme Corp") == "acme"
    assert normalize_name("ACME, INC.") == "acme"
    assert normalize_name("Acme Corporation") == "acme"
    assert normalize_name("Acme  Corp ") == "acme"
    assert normalize_name("globex industries") == "globex industries"


def test_domain_root_strips_prefix_and_tld():
    assert domain_root("acme.com") == "acme"
    assert domain_root("getacme.io") == "acme"
    assert domain_root("globex.com") == "globex"
    assert domain_root("trywhoever.app") == "whoever"


# --- duplicate detection -----------------------------------------------------


def test_acme_variants_flagged_as_high_severity():
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "Acme Corporation", "domain": "acme.com"},
        {"id": "acct_002", "name": "Acme Corp", "domain": "acme.com"},
        {"id": "acct_003", "name": "ACME CORP", "domain": "acme.com"},
        {"id": "acct_004", "name": "Initech", "domain": "initech.com"},
    ])
    issues = list(find_account_duplicates(accounts))
    # Acme trio → 3 pairs (1-2, 1-3, 2-3), all high severity
    acme_issues = [i for i in issues if "acme" in i.detail.get("matched_name", "").lower()]
    assert len(acme_issues) >= 3
    assert all(i.severity == "high" for i in acme_issues)


def test_initech_unrelated_to_acme():
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "Acme Corp", "domain": "acme.com"},
        {"id": "acct_002", "name": "Initech", "domain": "initech.com"},
    ])
    issues = list(find_account_duplicates(accounts))
    assert issues == []


def test_contact_exact_email_dup():
    contacts = pd.DataFrame([
        {"id": "cont_001", "first_name": "John", "last_name": "Smith",
         "email": "john.smith@acme.com", "account_id": "acct_001"},
        {"id": "cont_002", "first_name": "John", "last_name": "Smith",
         "email": "John.Smith@acme.com", "account_id": "acct_001"},
    ])
    issues = list(find_contact_duplicates(contacts))
    high = [i for i in issues if i.severity == "high"]
    assert len(high) == 1
    assert high[0].record_id == "cont_002"
    assert high[0].detail["method"] == "exact_email"


# --- schema validation -------------------------------------------------------


def test_invalid_phone_flagged():
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "X", "domain": "x.com", "phone": "(555) 123-4567",
         "state": "CA", "country": "US", "founded_year": 2010},
    ])
    issues = [i for i in validate_accounts(accounts) if i.field == "phone"]
    assert len(issues) == 1
    assert issues[0].severity == "high"


def test_valid_phone_passes():
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "X", "domain": "x.com", "phone": "+15551234567",
         "state": "CA", "country": "US", "founded_year": 2010},
    ])
    issues = [i for i in validate_accounts(accounts) if i.field == "phone"]
    assert issues == []


def test_invalid_email_flagged():
    contacts = pd.DataFrame([
        {"id": "cont_001", "email": "not-an-email", "phone": "+15551234567",
         "first_name": "X", "last_name": "Y", "account_id": "acct_001"},
    ])
    issues = [i for i in validate_contacts(contacts) if i.field == "email"]
    assert len(issues) == 1
    assert issues[0].severity == "high"


def test_zz_state_flagged():
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "X", "domain": "x.com", "phone": "+15551234567",
         "state": "ZZ", "country": "US", "founded_year": 2010},
    ])
    issues = [i for i in validate_accounts(accounts) if i.field == "state"]
    assert len(issues) == 1


# --- completeness ------------------------------------------------------------


def test_fill_rate_basic():
    df = pd.DataFrame({"a": ["x", "", "y", None], "b": [1, 2, 3, 4]})
    rates = fill_rate(df, ["a", "b"])
    assert rates["a"] == pytest.approx(0.5)
    assert rates["b"] == pytest.approx(1.0)


def test_blank_industry_flagged_medium():
    accounts = pd.DataFrame([
        {"id": "acct_001", "industry": "", "annual_revenue": 1000, "employee_count": 10,
         "country": "US", "phone": "+15551234567", "state": "CA"},
    ])
    issues = [i for i in check_completeness_accounts(accounts) if i.field == "industry"]
    assert len(issues) == 1
    assert issues[0].severity == "medium"


# --- orphans -----------------------------------------------------------------


def test_orphan_account_detected():
    accounts = pd.DataFrame([{"id": "acct_001"}, {"id": "acct_002"}])
    contacts = pd.DataFrame([{"id": "cont_001", "account_id": "acct_001"}])
    orphans = list(find_orphan_accounts(accounts, contacts))
    assert len(orphans) == 1
    assert orphans[0].record_id == "acct_002"


def test_orphan_contact_null_fk():
    accounts = pd.DataFrame([{"id": "acct_001"}])
    contacts = pd.DataFrame([{"id": "cont_001", "account_id": ""}])
    orphans = list(find_orphan_contacts(contacts, accounts))
    assert len(orphans) == 1
    assert orphans[0].severity == "high"


# --- end-to-end --------------------------------------------------------------


def test_run_audit_on_synthetic_messy_data():
    """A 5-row hand-crafted messy dataset must produce a non-trivial issue set."""
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "Acme Corp",  "domain": "acme.com",    "industry": "Software",
         "annual_revenue": 1_000_000, "employee_count": 100, "country": "US", "state": "CA",
         "phone": "+15551234567",  "owner_id": "owner_001", "lifecycle_stage": "Customer", "founded_year": 2010},
        {"id": "acct_002", "name": "ACME CORP",  "domain": "acme.com",    "industry": "",
         "annual_revenue": 1_000_000, "employee_count": 100, "country": "US", "state": "ZZ",
         "phone": "(555) 123-4567","owner_id": "inactive_user_01", "lifecycle_stage": "Lead", "founded_year": 2010},
        {"id": "acct_003", "name": "Globex",     "domain": "globex.com",  "industry": "Energy",
         "annual_revenue": 5_000_000, "employee_count": 500, "country": "US", "state": "NY",
         "phone": "+15559876543",  "owner_id": "owner_002", "lifecycle_stage": "Customer", "founded_year": 2030},
    ])
    contacts = pd.DataFrame([
        {"id": "cont_001", "first_name": "John", "last_name": "Smith",
         "email": "john.smith@acme.com", "phone": "+15551111111", "title": "VP",
         "account_id": "acct_001", "owner_id": "owner_001", "lifecycle_stage": "Customer"},
        {"id": "cont_002", "first_name": "John", "last_name": "Smith",
         "email": "John.Smith@acme.com", "phone": "+15551111111", "title": "VP",
         "account_id": "acct_001", "owner_id": "owner_001", "lifecycle_stage": "Customer"},
        {"id": "cont_003", "first_name": "X", "last_name": "Y",
         "email": "broken", "phone": "555-555-5555", "title": "",
         "account_id": "", "owner_id": "inactive_user_02", "lifecycle_stage": "Lead"},
    ])
    result = run_audit(accounts, contacts)
    types = result.counts_by_type
    # We expect at least one of each major category
    assert types.get("duplicate_candidate", 0) >= 1
    assert types.get("schema_violation", 0) >= 1
    assert types.get("completeness_gap", 0) >= 1
    assert types.get("inactive_owner", 0) >= 1
    assert types.get("orphan_contact", 0) >= 1
    assert types.get("lifecycle_inconsistency", 0) >= 1  # Globex Customer with no contacts
    assert result.has_critical  # at least one high severity
