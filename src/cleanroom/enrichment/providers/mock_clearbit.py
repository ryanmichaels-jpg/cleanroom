"""Mock Clearbit-style provider — synthetic but deterministic.

Simulates a mid-tier enrichment vendor with ~80% coverage. Always returns
the same data for the same input. Backbone of the dry-run demo since Apollo
only covers the hero domains by design.

Coverage: ~80% of domains get *some* data back (we hash the domain to decide).
Confidence: medium (since this is a "mock vendor" — pretend we'd score
Clearbit-like sources at medium).
"""

from __future__ import annotations

import hashlib
from typing import Any

from ._base import EnrichmentResult

_INDUSTRIES = [
    "Software", "Financial Services", "Healthcare", "Manufacturing",
    "Retail", "Media", "Education", "Real Estate", "Energy",
    "Consumer Goods", "Logistics", "Professional Services",
    "Construction", "Biotechnology", "Insurance",
]

_EMPLOYEE_BUCKETS = [25, 50, 100, 250, 500, 1000, 2500, 5000]


def _domain_hash_int(domain: str) -> int:
    return int(hashlib.sha256(domain.encode("utf-8")).hexdigest()[:8], 16)


class MockClearbitProvider:
    name = "mock_clearbit"

    def __init__(self, coverage: float = 0.80) -> None:
        self.coverage = coverage

    def enrich(self, record: dict) -> dict[str, EnrichmentResult]:
        domain = (record.get("domain") or "").strip().lower()
        if not domain:
            return {}
        h = _domain_hash_int(domain)

        # Coverage gate: ~20% of domains return nothing (simulates real-world misses).
        if (h % 100) >= int(self.coverage * 100):
            return {}

        industry = _INDUSTRIES[h % len(_INDUSTRIES)]
        employees = _EMPLOYEE_BUCKETS[(h >> 4) % len(_EMPLOYEE_BUCKETS)]
        # Revenue: rough heuristic — $500k per employee, with variation
        revenue = employees * (350_000 + (h >> 8) % 400_000)

        # Country defaults to US since the demo is US-only.
        return {
            "industry": EnrichmentResult(value=industry, confidence="medium", source=self.name),
            "employee_count": EnrichmentResult(value=employees, confidence="medium", source=self.name),
            "annual_revenue": EnrichmentResult(value=float(revenue), confidence="medium", source=self.name),
            "country": EnrichmentResult(value="US", confidence="medium", source=self.name),
        }
