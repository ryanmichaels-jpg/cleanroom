"""LLM tie-breaker for gray-zone duplicate pairs (rapidfuzz score 70–89).

Two modes:
  - live   → Claude Haiku 4.5 via the Anthropic SDK, JSON-structured output.
  - dry-run → deterministic rule (score >= 80 AND same domain root → same entity).

The dry-run mode is what runs in `python scripts/run_demo.py` so a recruiter
who cloned the repo gets reproducible numbers without an ANTHROPIC_API_KEY.
The live mode is what runs when ANTHROPIC_API_KEY is set + `--live` is passed.

Cap (LLM_TIEBREAK_CAP, default 500): in live mode we sample down to this many
pairs and log the cap-hit so the report can disclose it. Dry-run runs all pairs.
"""

from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from typing import Iterable

from ..audit._issue import Issue
from ..audit.duplicate_detector import domain_root, normalize_name


@dataclass
class TieBreakDecision:
    pair_record_id: str           # the duplicate-candidate Issue's record_id
    matched_with: str             # the canonical it was matched to
    same_entity: bool
    confidence: str               # "low" | "medium" | "high"
    reasoning: str
    source: str                   # "claude-haiku-4-5" | "dry-run-heuristic"


PROMPT_TEMPLATE = """You are deciding whether two CRM account records refer to the same real-world company.

Record A:
  name:           {name_a}
  domain:         {domain_a}
  industry:       {industry_a}
  state:          {state_a}
  employee_count: {emp_a}

Record B:
  name:           {name_b}
  domain:         {domain_b}
  industry:       {industry_b}
  state:          {state_b}
  employee_count: {emp_b}

rapidfuzz score on normalized names: {score}
domain roots match: {same_domain_root}

Return ONLY a JSON object, no preamble, no markdown:
{{"same_entity": true|false, "confidence": "low|medium|high", "reasoning": "<= 1 sentence"}}

Guidelines:
- Same entity = same legal company. Different brands of the same parent count if the domain root matches.
- Different industries on the same domain root usually means a data-entry error, not a different company.
- Wildly different employee counts (50 vs 5000) on the same domain root usually means stale data, not a different company.
- If unsure, lean toward false with confidence "low".
"""


def _build_prompt(issue: Issue, accounts_by_id: dict[str, dict]) -> str:
    a = accounts_by_id.get(issue.detail.get("matched_with", ""), {})
    b = accounts_by_id.get(issue.record_id, {})
    return PROMPT_TEMPLATE.format(
        name_a=a.get("name", ""),
        domain_a=a.get("domain", ""),
        industry_a=a.get("industry", "") or "(blank)",
        state_a=a.get("state", "") or "(blank)",
        emp_a=a.get("employee_count", "") or "(blank)",
        name_b=b.get("name", ""),
        domain_b=b.get("domain", ""),
        industry_b=b.get("industry", "") or "(blank)",
        state_b=b.get("state", "") or "(blank)",
        emp_b=b.get("employee_count", "") or "(blank)",
        score=issue.detail.get("score", 0),
        same_domain_root=issue.detail.get("same_domain_root", False),
    )


def _dry_run_decide(issue: Issue, accounts_by_id: dict[str, dict]) -> TieBreakDecision:
    a = accounts_by_id.get(issue.detail.get("matched_with", ""), {})
    b = accounts_by_id.get(issue.record_id, {})
    score = float(issue.detail.get("score", 0))
    same_dr = bool(issue.detail.get("same_domain_root", False))
    same_norm = normalize_name(a.get("name", "")) == normalize_name(b.get("name", ""))

    if score >= 85 and same_dr:
        return TieBreakDecision(
            pair_record_id=issue.record_id,
            matched_with=a.get("id", issue.detail.get("matched_with", "")),
            same_entity=True,
            confidence="high",
            reasoning=f"Normalized name score {score:.0f} + matching domain root '{domain_root(a.get('domain', ''))}'.",
            source="dry-run-heuristic",
        )
    if score >= 80 and same_norm:
        return TieBreakDecision(
            pair_record_id=issue.record_id,
            matched_with=a.get("id", issue.detail.get("matched_with", "")),
            same_entity=True,
            confidence="medium",
            reasoning=f"Normalized names identical (score {score:.0f}); domain roots differ.",
            source="dry-run-heuristic",
        )
    return TieBreakDecision(
        pair_record_id=issue.record_id,
        matched_with=a.get("id", issue.detail.get("matched_with", "")),
        same_entity=False,
        confidence="low",
        reasoning=f"Score {score:.0f} below merge threshold; surfaces and domains diverge.",
        source="dry-run-heuristic",
    )


def _live_decide(issue: Issue, accounts_by_id: dict[str, dict], anthropic_client) -> TieBreakDecision:
    prompt = _build_prompt(issue, accounts_by_id)
    msg = anthropic_client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # Strip code-fence if Claude returns one despite instructions.
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Defensive fallback: treat as "not sure → don't merge"
        payload = {"same_entity": False, "confidence": "low", "reasoning": f"unparsable LLM output: {text[:80]}"}
    return TieBreakDecision(
        pair_record_id=issue.record_id,
        matched_with=accounts_by_id.get(issue.detail.get("matched_with", ""), {}).get(
            "id", issue.detail.get("matched_with", "")
        ),
        same_entity=bool(payload.get("same_entity", False)),
        confidence=str(payload.get("confidence", "low")),
        reasoning=str(payload.get("reasoning", "")),
        source="claude-haiku-4-5",
    )


def resolve_gray_zone(
    issues: Iterable[Issue],
    accounts_by_id: dict[str, dict],
    *,
    live: bool = False,
    cap: int | None = None,
    seed: int = 42,
) -> tuple[list[TieBreakDecision], dict]:
    """Run tie-break decisions over the gray-zone (medium-severity) duplicate
    candidates. Returns (decisions, telemetry)."""
    cap = cap if cap is not None else int(os.getenv("LLM_TIEBREAK_CAP", "500"))
    gray_zone = [
        i for i in issues
        if i.issue_type == "duplicate_candidate"
        and i.severity == "medium"
        and i.record_type == "account"
    ]
    rng = random.Random(seed)
    capped = False
    if len(gray_zone) > cap:
        rng.shuffle(gray_zone)
        gray_zone = gray_zone[:cap]
        capped = True

    if live:
        try:
            from anthropic import Anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("anthropic SDK not installed — pip install -e .") from e
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY not set; cannot run live tie-break")
        client = Anthropic()
        decide = lambda issue: _live_decide(issue, accounts_by_id, client)  # noqa: E731
    else:
        decide = lambda issue: _dry_run_decide(issue, accounts_by_id)  # noqa: E731

    decisions = [decide(issue) for issue in gray_zone]
    telemetry = {
        "n_gray_zone_pairs": len(gray_zone),
        "cap": cap,
        "cap_hit": capped,
        "n_same_entity_true": sum(1 for d in decisions if d.same_entity),
        "n_same_entity_false": sum(1 for d in decisions if not d.same_entity),
        "mode": "live" if live else "dry-run",
    }
    return decisions, telemetry
