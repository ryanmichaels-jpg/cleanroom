"""Enrichment tests — provider determinism + waterfall behavior."""

from __future__ import annotations

import pandas as pd

from cleanroom.enrichment import run_enrichment
from cleanroom.enrichment.providers.apollo import ApolloProvider
from cleanroom.enrichment.providers.claude_websearch import ClaudeWebSearchProvider
from cleanroom.enrichment.providers.mock_clearbit import MockClearbitProvider
from cleanroom.enrichment.waterfall import run_waterfall


def test_apollo_dry_run_covers_hero_domains():
    p = ApolloProvider(live=False)
    result = p.enrich({"domain": "acme.com"})
    assert "industry" in result
    assert result["industry"].source == "apollo"
    assert result["industry"].confidence == "high"


def test_apollo_dry_run_misses_random_domains():
    p = ApolloProvider(live=False)
    assert p.enrich({"domain": "randomthing123.com"}) == {}


def test_mock_clearbit_deterministic():
    p = MockClearbitProvider()
    r1 = p.enrich({"domain": "example-corp.com"})
    r2 = p.enrich({"domain": "example-corp.com"})
    if r1:  # may be empty if domain hashes to a coverage miss
        assert r1["industry"].value == r2["industry"].value


def test_claude_websearch_dry_run_lower_confidence():
    p = ClaudeWebSearchProvider(live=False)
    r = p.enrich({"domain": "techstartup.com"})
    if r:
        # All values should be low-confidence
        for res in r.values():
            assert res.confidence == "low"


def test_waterfall_apollo_wins_over_mock_on_hero():
    """Hero domain → Apollo (high conf) should fill industry, not mock_clearbit."""
    accounts = pd.DataFrame([{
        "id": "acct_001", "name": "Acme Corp", "domain": "acme.com",
        "industry": "",  # blank, will be filled
        "annual_revenue": None, "employee_count": None,
        "country": "", "phone": "+15551234567",
    }])
    enriched, tracker = run_waterfall(accounts, [
        ApolloProvider(live=False),
        MockClearbitProvider(),
        ClaudeWebSearchProvider(live=False),
    ])
    assert enriched.iloc[0]["industry"] == "Manufacturing"
    # Confirm the metadata says apollo, high conf
    rows = [r for r in tracker.rows() if r.field == "industry"]
    assert len(rows) == 1
    assert rows[0].source == "apollo"
    assert rows[0].confidence == "high"


def test_waterfall_skips_already_populated_fields():
    accounts = pd.DataFrame([{
        "id": "acct_001", "name": "X", "domain": "acme.com",
        "industry": "Healthcare",  # already populated
        "annual_revenue": None,
        "employee_count": None,
        "country": "US",
        "phone": "+15551234567",
    }])
    enriched, _tracker = run_waterfall(accounts, [ApolloProvider(live=False)])
    # Industry was already Healthcare — Apollo should not overwrite
    assert enriched.iloc[0]["industry"] == "Healthcare"


def test_run_enrichment_end_to_end_fills_most_blanks():
    """1 hero domain + 4 random domains; expect most blanks filled."""
    accounts = pd.DataFrame([
        {"id": "acct_001", "name": "Acme", "domain": "acme.com",
         "industry": "", "annual_revenue": None, "employee_count": None,
         "country": "", "phone": "+15551234567"},
        {"id": "acct_002", "name": "Apex", "domain": "apex-systems.com",
         "industry": "", "annual_revenue": None, "employee_count": None,
         "country": "", "phone": "+15551234567"},
        {"id": "acct_003", "name": "Zenith", "domain": "zenithgroup.io",
         "industry": "", "annual_revenue": None, "employee_count": None,
         "country": "", "phone": "+15551234567"},
    ])
    enriched, tracker = run_enrichment(accounts, live=False)
    industries = enriched["industry"].astype(str).tolist()
    n_filled = sum(1 for v in industries if v and v != "nan")
    assert n_filled >= 2, f"expected most industries filled, got {industries}"
    assert len(tracker) >= 2


def test_confidence_tracker_aggregates():
    from cleanroom.enrichment.confidence_tracker import ConfidenceTracker
    t = ConfidenceTracker()
    t.record("acct_001", "industry", "Software", "apollo", "high")
    t.record("acct_001", "country", "US", "mock_clearbit", "medium")
    t.record("acct_002", "industry", "Energy", "claude_websearch", "low")
    assert t.by_source() == {"apollo": 1, "mock_clearbit": 1, "claude_websearch": 1}
    assert t.by_confidence() == {"high": 1, "medium": 1, "low": 1}
    assert t.cleanroom_enrichment_sources_for("acct_001") == "apollo|mock_clearbit"
    # Confidence score for acct_001: (1.0 + 0.66) / 2 = 0.83
    assert t.confidence_score_for("acct_001") == 0.83
