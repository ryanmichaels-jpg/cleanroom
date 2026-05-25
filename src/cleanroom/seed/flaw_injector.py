"""Deterministic flaw injection.

Takes clean accounts + contacts DataFrames and mutates them to look like a
real CRM: dupes, blanks, schema violations, orphans, inactive owners.

Every mutation reads its rate from seed_config.yaml. Every random pick uses
the seeded `random.Random` instance so the same input + same seed always
produces the same messy output.
"""

from __future__ import annotations

import random
import re
from typing import Any

import pandas as pd

from .generator import (
    _e164_phone,
    _stable_id,
    INACTIVE_OWNER_IDS,
)

# --- Duplicate-variant transformations ---------------------------------------


def _case_whitespace_variant(name: str, rng: random.Random) -> str:
    transform = rng.choice(["upper", "lower", "extra_space", "trailing"])
    if transform == "upper":
        return name.upper()
    if transform == "lower":
        return name.lower()
    if transform == "extra_space":
        return name.replace(" ", "  ")
    return name + " "


_LEGAL_SUFFIXES = [", Inc.", " LLC", " Corp", " Corporation", " Co.", " Ltd."]


def _legal_suffix_variant(name: str, rng: random.Random) -> str:
    # Strip existing suffix if present, then add a different one.
    base = name
    for suf in _LEGAL_SUFFIXES:
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    new_suf = rng.choice(_LEGAL_SUFFIXES)
    return f"{base}{new_suf}"


def _typo_variant(name: str, rng: random.Random) -> str:
    """Introduce a 1-character typo: double letter, swap, or near-miss."""
    if len(name) < 4:
        return name + "e"
    pos = rng.randint(1, len(name) - 2)
    op = rng.choice(["double", "swap", "near"])
    if op == "double":
        return name[:pos] + name[pos] + name[pos:]
    if op == "swap":
        return name[:pos] + name[pos + 1] + name[pos] + name[pos + 2 :]
    # near-miss: e -> i, o -> 0 (no), m -> n, etc.
    near = {"e": "i", "i": "e", "m": "n", "n": "m", "o": "u", "u": "o", "a": "e"}
    ch = name[pos].lower()
    if ch in near:
        replacement = near[ch]
        if name[pos].isupper():
            replacement = replacement.upper()
        return name[:pos] + replacement + name[pos + 1 :]
    return name + name[-1]


def _domain_variant(domain: str, rng: random.Random) -> str:
    """Common domain shape variants: prefix with get/try, swap tld."""
    if not domain or "." not in domain:
        return domain
    base, _, tld = domain.rpartition(".")
    op = rng.choice(["prefix", "tld"])
    if op == "prefix":
        prefix = rng.choice(["get", "try", "use", "the"])
        return f"{prefix}{base}.{tld}"
    new_tld = rng.choice([t for t in ["com", "io", "co", "ai", "app"] if t != tld])
    return f"{base}.{new_tld}"


# --- Duplicate account injection ---------------------------------------------


def inject_account_duplicates(
    accounts: pd.DataFrame,
    config: dict[str, Any],
    rng: random.Random,
) -> pd.DataFrame:
    """For ~`account_dup_pct` of accounts, append 1–`max_dup_copies` extra rows
    with the same canonical identity but different surface forms."""
    dup_cfg = config["duplicates"]
    pct = dup_cfg["account_dup_pct"]
    max_copies = dup_cfg["max_dup_copies"]
    patterns = list(dup_cfg["pattern_weights"].keys())
    pattern_weights = list(dup_cfg["pattern_weights"].values())

    n_to_dup = int(len(accounts) * pct)
    targets = rng.sample(range(len(accounts)), k=n_to_dup)

    new_rows: list[dict[str, Any]] = []
    next_idx = len(accounts) + 1

    for tgt in targets:
        original = accounts.iloc[tgt].to_dict()
        n_copies = rng.randint(1, max_copies)
        for _ in range(n_copies):
            pattern = rng.choices(patterns, weights=pattern_weights)[0]
            dup = dict(original)
            dup["id"] = _stable_id("acct", next_idx)
            next_idx += 1

            name = original["name"]
            domain = original["domain"]
            if pattern == "case_whitespace":
                dup["name"] = _case_whitespace_variant(name, rng)
            elif pattern == "legal_suffix":
                dup["name"] = _legal_suffix_variant(name, rng)
            elif pattern == "typo":
                dup["name"] = _typo_variant(name, rng)
            elif pattern == "domain_variant":
                dup["domain"] = _domain_variant(domain, rng)
            new_rows.append(dup)

    if new_rows:
        accounts = pd.concat([accounts, pd.DataFrame(new_rows)], ignore_index=True)
    return accounts


# --- Contact-level duplicate injection ---------------------------------------


def inject_contact_duplicates(
    contacts: pd.DataFrame,
    config: dict[str, Any],
    rng: random.Random,
) -> pd.DataFrame:
    """Variant-email dupes (john.smith@ + jsmith@) and name-swap dupes."""
    dup_cfg = config["contact_duplicates"]
    n = len(contacts)
    n_variant = int(n * dup_cfg["variant_email_pct"])
    n_swap = int(n * dup_cfg["name_swap_pct"])

    new_rows: list[dict[str, Any]] = []
    next_idx = n + 1

    variant_targets = rng.sample(range(n), k=n_variant)
    for tgt in variant_targets:
        original = contacts.iloc[tgt].to_dict()
        if not isinstance(original.get("email"), str) or "@" not in original["email"]:
            continue
        local, _, domain = original["email"].partition("@")
        # Common variant: drop the period -> jsmith style
        first = (original.get("first_name") or "").lower()
        last = (original.get("last_name") or "").lower()
        if first and last:
            variant_local = f"{first[0]}{last}"
        else:
            variant_local = local.replace(".", "")
        dup = dict(original)
        dup["id"] = _stable_id("cont", next_idx)
        dup["email"] = f"{variant_local}@{domain}"
        next_idx += 1
        new_rows.append(dup)

    swap_targets = rng.sample(range(n), k=n_swap)
    for tgt in swap_targets:
        original = contacts.iloc[tgt].to_dict()
        if not original.get("first_name") or not original.get("last_name"):
            continue
        dup = dict(original)
        dup["id"] = _stable_id("cont", next_idx)
        dup["first_name"] = original["last_name"]
        dup["last_name"] = original["first_name"]
        # Email gets swapped to match the swapped name.
        if isinstance(original.get("email"), str) and "@" in original["email"]:
            _, _, domain = original["email"].partition("@")
            dup["email"] = f"{original['last_name'].lower()}.{original['first_name'].lower()}@{domain}"
        next_idx += 1
        new_rows.append(dup)

    if new_rows:
        contacts = pd.concat([contacts, pd.DataFrame(new_rows)], ignore_index=True)
    return contacts


# --- Completeness gaps -------------------------------------------------------


def inject_completeness_gaps(
    df: pd.DataFrame,
    field_rates: dict[str, float],
    rng: random.Random,
) -> pd.DataFrame:
    """For each (field, rate) pair, blank that field on `rate` fraction of rows."""
    for field, rate in field_rates.items():
        if field not in df.columns:
            continue
        n_blank = int(len(df) * rate)
        targets = rng.sample(range(len(df)), k=n_blank)
        # Use empty string for object columns, NaN for numeric — keep CSV legible.
        if pd.api.types.is_numeric_dtype(df[field]):
            df.loc[df.index[targets], field] = pd.NA
        else:
            df.loc[df.index[targets], field] = ""
    return df


# --- Schema violations -------------------------------------------------------


_BAD_PHONE_FORMATS = [
    lambda area, pre, line: f"({area}) {pre}-{line:04d}",
    lambda area, pre, line: f"{area}-{pre}-{line:04d}",
    lambda area, pre, line: f"{area}.{pre}.{line:04d}",
    lambda area, pre, line: f"{area}{pre}{line:04d}",  # missing +1
    lambda area, pre, line: f"+1 {area} {pre} {line:04d}",
]


def _to_bad_phone(_phone: str, rng: random.Random) -> str:
    area = rng.randint(200, 989)
    pre = rng.randint(200, 989)
    line = rng.randint(0, 9999)
    return rng.choice(_BAD_PHONE_FORMATS)(area, pre, line)


def _to_bad_email(email: str, rng: random.Random) -> str:
    if not isinstance(email, str) or not email:
        return email
    op = rng.choice(["drop_at", "trailing_ws", "double_dot", "space_inside"])
    if op == "drop_at":
        return email.replace("@", "", 1)
    if op == "trailing_ws":
        return email + " "
    if op == "double_dot":
        return email.replace("@", "@.", 1)
    if op == "space_inside":
        return email.replace("@", " @", 1)
    return email


def inject_schema_violations(
    accounts: pd.DataFrame,
    contacts: pd.DataFrame,
    config: dict[str, Any],
    rng: random.Random,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    sv = config["schema_violations"]
    now_year = 2026  # matches GenContext anchor

    # Invalid state codes
    n = int(len(accounts) * sv["invalid_state_code"])
    targets = rng.sample(range(len(accounts)), k=n)
    accounts.loc[accounts.index[targets], "state"] = "ZZ"

    # Bad phone formats — only on rows where phone is non-empty
    for df in (accounts, contacts):
        phone_populated = df.index[df["phone"].astype(str).str.startswith("+1")].tolist()
        n = int(len(phone_populated) * sv["bad_phone_format"])
        if n and phone_populated:
            targets = rng.sample(phone_populated, k=min(n, len(phone_populated)))
            df.loc[targets, "phone"] = [_to_bad_phone(df.at[t, "phone"], rng) for t in targets]

    # Future founded years
    n = int(len(accounts) * sv["future_founded_year"])
    targets = rng.sample(range(len(accounts)), k=n)
    for t in targets:
        accounts.at[accounts.index[t], "founded_year"] = now_year + rng.randint(1, 10)

    # Invalid emails — only on rows where email is non-empty and well-formed
    if "email" in contacts.columns:
        email_populated = contacts.index[
            contacts["email"].astype(str).str.contains("@", na=False)
        ].tolist()
        n = int(len(email_populated) * sv["invalid_email"])
        if n and email_populated:
            targets = rng.sample(email_populated, k=min(n, len(email_populated)))
            contacts.loc[targets, "email"] = [
                _to_bad_email(contacts.at[t, "email"], rng) for t in targets
            ]

    return accounts, contacts


# --- Lifecycle / ownership errors --------------------------------------------


def inject_lifecycle_and_ownership(
    accounts: pd.DataFrame,
    contacts: pd.DataFrame,
    config: dict[str, Any],
    rng: random.Random,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lc = config["lifecycle"]

    # Inactive owners across both tables
    for df in (accounts, contacts):
        n = int(len(df) * lc["inactive_owner_pct"])
        targets = rng.sample(range(len(df)), k=n)
        df.loc[df.index[targets], "owner_id"] = [rng.choice(INACTIVE_OWNER_IDS) for _ in targets]

    # Orphan accounts: pick `orphan_account_pct` accounts and delete all their contacts
    n_orphans = int(len(accounts) * lc["orphan_account_pct"])
    orphan_idxs = rng.sample(range(len(accounts)), k=n_orphans)
    orphan_acct_ids = set(accounts.iloc[orphan_idxs]["id"].tolist())
    contacts = contacts[~contacts["account_id"].isin(orphan_acct_ids)].reset_index(drop=True)

    # NULL account_id on some contacts
    n_null = int(len(contacts) * lc["null_account_id_pct"])
    if n_null:
        targets = rng.sample(range(len(contacts)), k=n_null)
        contacts.loc[contacts.index[targets], "account_id"] = ""

    # Closed-won accounts with no contacts (post-orphan, so these may overlap)
    n_cw = int(len(accounts) * lc["closed_won_no_contact_pct"])
    cw_idxs = rng.sample(range(len(accounts)), k=n_cw)
    accounts.loc[accounts.index[cw_idxs], "lifecycle_stage"] = "Customer"
    cw_acct_ids = set(accounts.iloc[cw_idxs]["id"].tolist())
    contacts = contacts[~contacts["account_id"].isin(cw_acct_ids)].reset_index(drop=True)

    return accounts, contacts


# --- Top-level orchestrator --------------------------------------------------


def inject_all_flaws(
    accounts: pd.DataFrame,
    contacts: pd.DataFrame,
    config: dict[str, Any],
    rng: random.Random,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply every flaw category in a fixed order. Order matters for reproducibility."""
    # 1. Duplicates first — added rows become candidates for blanks/violations below.
    accounts = inject_account_duplicates(accounts, config, rng)
    contacts = inject_contact_duplicates(contacts, config, rng)

    # 2. Schema violations — apply to populated values, before we blank some out.
    accounts, contacts = inject_schema_violations(accounts, contacts, config, rng)

    # 3. Completeness gaps — blank fields per config rates.
    accounts = inject_completeness_gaps(accounts, config["completeness_gaps"]["account"], rng)
    contacts = inject_completeness_gaps(contacts, config["completeness_gaps"]["contact"], rng)

    # 4. Lifecycle / ownership errors — orphans, null FKs, inactive owners.
    accounts, contacts = inject_lifecycle_and_ownership(accounts, contacts, config, rng)

    return accounts, contacts
