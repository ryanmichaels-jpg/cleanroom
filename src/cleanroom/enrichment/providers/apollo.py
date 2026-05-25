"""Apollo provider — primary enrichment source.

Live mode: calls Apollo's /v1/organizations/enrich endpoint with the domain.
Dry-run mode: returns canned data for hero domains (acme.com, globex.com,
initech.com); returns nothing for the rest. This is deliberately narrow so the
demo legibly shows Apollo "covering some, missing most" — which is the real
shape of any single-provider waterfall and motivates the fallback chain.

Cache: live results are stored in data/enrichment/cache.json keyed by domain
so repeated dev runs don't burn the 100-credits/mo free tier.

Pattern borrowed from gooseworks-ai/goose-skills `apollo-lead-finder`
(two-phase match → enrich, cache the manifest between phases).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ._base import EnrichmentResult


# Canned enrichment data for the hero dupes — makes the demo Loom legible
# without requiring an APOLLO_API_KEY in dry-run mode.
_HERO_CANNED: dict[str, dict] = {
    "acme.com": {
        "industry": "Manufacturing",
        "annual_revenue": 87_000_000,
        "employee_count": 850,
        "country": "US",
        "phone": "+15551110001",
    },
    "globex.com": {
        "industry": "Energy",
        "annual_revenue": 142_000_000,
        "employee_count": 1200,
        "country": "US",
        "phone": "+15551110002",
    },
    "initech.com": {
        "industry": "Software",
        "annual_revenue": 35_000_000,
        "employee_count": 320,
        "country": "US",
        "phone": "+15551110003",
    },
}


class ApolloProvider:
    name = "apollo"
    _ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"

    def __init__(
        self,
        api_key: str | None = None,
        cache_path: Path | None = None,
        live: bool = False,
    ) -> None:
        self.api_key = api_key or os.getenv("APOLLO_API_KEY", "")
        self.cache_path = cache_path or Path("data/enrichment/cache.json")
        self.live = live and bool(self.api_key)
        self._cache: dict[str, dict] = self._load_cache()

    def _load_cache(self) -> dict[str, dict]:
        if not self.cache_path.exists():
            return {}
        try:
            return json.loads(self.cache_path.read_text())
        except json.JSONDecodeError:
            return {}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, indent=2, default=str))

    def _live_lookup(self, domain: str) -> dict | None:
        import httpx
        try:
            resp = httpx.get(
                self._ENRICH_URL,
                params={"domain": domain},
                headers={
                    "x-api-key": self.api_key,
                    "Cache-Control": "no-cache",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("organization") or {}
            return {
                "industry": data.get("industry"),
                "annual_revenue": data.get("annual_revenue") or data.get("estimated_num_employees"),
                "employee_count": data.get("estimated_num_employees") or data.get("publicly_traded_symbol"),
                "country": data.get("country") or "US",
                "phone": data.get("phone") or data.get("sanitized_phone"),
            }
        except Exception:
            return None

    def enrich(self, record: dict) -> dict[str, EnrichmentResult]:
        domain = (record.get("domain") or "").strip().lower()
        if not domain:
            return {}

        cache_key = f"domain:{domain}"
        if cache_key in self._cache:
            data = self._cache[cache_key]
        elif self.live:
            data = self._live_lookup(domain)
            if data is not None:
                self._cache[cache_key] = data
                self._save_cache()
            else:
                return {}
        else:
            data = _HERO_CANNED.get(domain)
            if data is None:
                return {}

        out: dict[str, EnrichmentResult] = {}
        for field, value in (data or {}).items():
            if value is None or value == "":
                continue
            out[field] = EnrichmentResult(value=value, confidence="high", source=self.name)
        return out
