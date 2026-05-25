"""Enrichment: Clay-style provider waterfall.

Borrows the provider-fallback-chain + per-field confidence patterns from
gooseworks-ai/goose-skills `inbound-lead-enrichment`. Each provider runs in
sequence; the first one that returns a non-blank value for a given (record,
field) wins. The per-field source + confidence + timestamp are stored in a
sidecar (ConfidenceTracker) and surfaced on the pushed Salesforce records
as `cleanroom_enrichment_sources__c` + `cleanroom_confidence_score__c`.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .confidence_tracker import ConfidenceTracker
from .providers.apollo import ApolloProvider
from .providers.claude_websearch import ClaudeWebSearchProvider
from .providers.mock_clearbit import MockClearbitProvider
from .waterfall import run_waterfall


def default_providers(live: bool = False, cache_path: Path | None = None) -> list:
    """Build the standard 3-provider chain. Live mode lets Apollo + Claude hit
    real APIs; dry-run keeps everything deterministic."""
    return [
        ApolloProvider(live=live, cache_path=cache_path),
        MockClearbitProvider(),
        ClaudeWebSearchProvider(live=live),
    ]


def run_enrichment(
    accounts: pd.DataFrame,
    *,
    live: bool = False,
    cache_path: Path | None = None,
) -> tuple[pd.DataFrame, ConfidenceTracker]:
    """Public entry point used by the CLI + the demo runner."""
    providers = default_providers(live=live, cache_path=cache_path)
    return run_waterfall(accounts, providers)


def write_enrichment_outputs(
    out_dir: Path,
    enriched: pd.DataFrame,
    tracker: ConfidenceTracker,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    enriched.to_csv(out_dir / "accounts_enriched.csv", index=False)
    tracker.to_jsonl(out_dir / "field_metadata.jsonl")
