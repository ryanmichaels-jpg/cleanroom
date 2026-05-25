"""Provider protocol — what every enrichment source implements.

A provider takes one record (account dict) and returns a dict of
`field -> EnrichmentResult` for whichever fields it has data for. The
waterfall picks only the fields that are currently blank on the record.

Providers MUST be deterministic in `dry_run` mode (same input → same output)
so the demo's report numbers are reproducible across clones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class EnrichmentResult:
    value: str | float | int | None
    confidence: str   # "high" | "medium" | "low"
    source: str       # provider name


class Provider(Protocol):
    name: str

    def enrich(self, record: dict) -> dict[str, EnrichmentResult]:
        """Return field -> result for fields this provider can supply."""
        ...
