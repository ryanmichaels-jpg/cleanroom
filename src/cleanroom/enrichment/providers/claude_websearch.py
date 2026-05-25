"""Claude as LLM-of-last-resort — fills fields nothing else covers.

Live mode: real Anthropic SDK call to Sonnet 4.6 with a tight prompt asking
to infer industry/size/country for a domain. (A future enhancement would
plug Claude's web_search tool in here; for now, this is domain inference
from the model's prior, not live web research — and the docstring says so.)

Dry-run mode: deterministic synthetic guess (high coverage, low confidence)
so the demo can run offline.
"""

from __future__ import annotations

import hashlib
import json
import os

from ._base import EnrichmentResult


_FALLBACK_INDUSTRIES = ["Professional Services", "Software", "Consumer Goods", "Healthcare"]

PROMPT = """You are filling a CRM record. Based on the domain alone, what's your best guess for:
- industry (one of: Software, Financial Services, Healthcare, Manufacturing, Retail, Media, Education, Real Estate, Energy, Telecommunications, Consumer Goods, Logistics, Hospitality, Professional Services, Construction, Automotive, Biotechnology, Insurance)
- employee_count (integer estimate)
- country ("US" if not obviously elsewhere)

Domain: {domain}

Return ONLY a JSON object: {{"industry": "...", "employee_count": N, "country": "US"}}
If you cannot guess at all (random-looking domain), return {{"industry": null, "employee_count": null, "country": "US"}}."""


def _hash_int(s: str) -> int:
    return int(hashlib.sha256(s.encode("utf-8")).hexdigest()[:8], 16)


class ClaudeWebSearchProvider:
    name = "claude_websearch"

    def __init__(self, live: bool = False, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.live = live and bool(self.api_key)

    def _dry_run_guess(self, domain: str) -> dict:
        h = _hash_int(domain)
        # Lower coverage than mock_clearbit (~70%) and lower confidence.
        if (h % 100) >= 70:
            return {}
        industry = _FALLBACK_INDUSTRIES[h % len(_FALLBACK_INDUSTRIES)]
        employees = [50, 100, 200, 500][(h >> 4) % 4]
        return {"industry": industry, "employee_count": employees, "country": "US"}

    def _live_guess(self, domain: str) -> dict:
        try:
            from anthropic import Anthropic
        except ImportError:
            return {}
        client = Anthropic()
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=120,
                messages=[{"role": "user", "content": PROMPT.format(domain=domain)}],
            )
            text = msg.content[0].text.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            data = json.loads(text)
            return {k: v for k, v in data.items() if v is not None}
        except Exception:
            return {}

    def enrich(self, record: dict) -> dict[str, EnrichmentResult]:
        domain = (record.get("domain") or "").strip().lower()
        if not domain:
            return {}
        data = self._live_guess(domain) if self.live else self._dry_run_guess(domain)
        out: dict[str, EnrichmentResult] = {}
        for field, value in (data or {}).items():
            if value is None or value == "":
                continue
            out[field] = EnrichmentResult(value=value, confidence="low", source=self.name)
        return out
