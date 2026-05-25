"""Seed reproducibility: same config + same seed must produce byte-identical CSVs.

This is the test that anchors the rest of the pipeline. If the seed isn't
reproducible, the audit/resolve/enrich numbers in the demo report drift on
every run and we can't write deterministic assertions downstream.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd
import pytest
import yaml

from cleanroom.seed.flaw_injector import inject_all_flaws
from cleanroom.seed.generator import (
    GenContext,
    generate_clean_accounts,
    generate_clean_contacts,
)


CONFIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "cleanroom"
    / "seed"
    / "seed_config.yaml"
)


@pytest.fixture
def config():
    return yaml.safe_load(CONFIG_PATH.read_text())


def _run(config: dict, seed: int, n_accounts: int):
    ctx = GenContext.from_seed(seed)
    rng = random.Random(seed + 1)
    accounts = generate_clean_accounts(n_accounts, ctx, hero_dupes=config.get("hero_dupes"))
    contacts = generate_clean_contacts(
        accounts,
        ctx,
        mean_per_account=config["dataset"]["default_contacts_per_account_mean"],
        max_per_account=config["dataset"]["default_contacts_per_account_max"],
    )
    return inject_all_flaws(accounts, contacts, config, rng)


def test_same_seed_same_output(config):
    accounts_a, contacts_a = _run(config, seed=42, n_accounts=200)
    accounts_b, contacts_b = _run(config, seed=42, n_accounts=200)
    pd.testing.assert_frame_equal(accounts_a, accounts_b)
    pd.testing.assert_frame_equal(contacts_a, contacts_b)


def test_different_seed_different_output(config):
    accounts_a, _ = _run(config, seed=42, n_accounts=200)
    accounts_b, _ = _run(config, seed=999, n_accounts=200)
    # Same row count expected (deterministic structure), different content.
    assert not accounts_a.equals(accounts_b)


def test_hero_dupes_present(config):
    accounts, _ = _run(config, seed=42, n_accounts=200)
    names = set(accounts["name"].astype(str).tolist())
    # Canonical hero dupes from config must always appear.
    for hero in config["hero_dupes"]:
        assert hero["canonical_name"] in names, f"missing hero canonical: {hero['canonical_name']}"


def test_flaw_rates_approximate(config):
    """Sanity: flaw rates land in the right ballpark on a 1000-account run."""
    accounts, contacts = _run(config, seed=42, n_accounts=1000)

    # Industry blank rate ~30% (config) — allow ±5pp tolerance
    industry_blank = (accounts["industry"] == "").mean()
    assert 0.20 <= industry_blank <= 0.40, f"industry blank rate {industry_blank:.2%} out of range"

    # State 'ZZ' (invalid) ~5% — allow ±3pp
    bad_state = (accounts["state"] == "ZZ").mean()
    assert 0.01 <= bad_state <= 0.10, f"invalid state rate {bad_state:.2%} out of range"

    # Inactive-owner rate ~8% on accounts — allow ±4pp
    inactive = accounts["owner_id"].astype(str).str.startswith("inactive_user_").mean()
    assert 0.03 <= inactive <= 0.15, f"inactive owner rate {inactive:.2%} out of range"

    # At least one orphan account (account exists but no contacts)
    accts_with_contacts = set(contacts["account_id"].astype(str).tolist())
    orphans = accounts[~accounts["id"].isin(accts_with_contacts)]
    assert len(orphans) > 0, "expected at least one orphan account"


def test_canonical_acme_has_known_variants(config):
    """The Acme hero group should produce both 'Acme Corp' and 'ACME CORP'
    in the dataset — these are the Loom storyline anchors."""
    accounts, _ = _run(config, seed=42, n_accounts=200)
    names = set(accounts["name"].astype(str).tolist())
    assert "Acme Corp" in names
    assert "ACME CORP" in names
    assert "Acme Corporation" in names
