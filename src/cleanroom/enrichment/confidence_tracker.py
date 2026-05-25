"""Per-field sidecar metadata: which source filled this, with what confidence, when.

Borrowed shape: gooseworks-ai/goose-skills `inbound-lead-enrichment` records
per-field `high|medium|low` + a `sources_used` array. We collapse to one
row per (record_id, field) since we always pick the first non-null in the
waterfall — multi-source aggregation is a v2 problem.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class FieldMetadata:
    record_id: str
    field: str
    value: str
    source: str
    confidence: str
    timestamp: str  # ISO-8601 UTC


class ConfidenceTracker:
    def __init__(self) -> None:
        self._rows: list[FieldMetadata] = []

    def record(self, record_id: str, field: str, value, source: str, confidence: str) -> None:
        self._rows.append(
            FieldMetadata(
                record_id=record_id,
                field=field,
                value="" if value is None else str(value),
                source=source,
                confidence=confidence,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )

    def __len__(self) -> int:
        return len(self._rows)

    def rows(self) -> list[FieldMetadata]:
        return list(self._rows)

    def by_source(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._rows:
            out[r.source] = out.get(r.source, 0) + 1
        return out

    def by_field(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._rows:
            out[r.field] = out.get(r.field, 0) + 1
        return out

    def by_confidence(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._rows:
            out[r.confidence] = out.get(r.confidence, 0) + 1
        return out

    def to_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            for r in self._rows:
                f.write(json.dumps(asdict(r)) + "\n")

    def cleanroom_enrichment_sources_for(self, record_id: str) -> str:
        """Pipe-delimited list of sources that contributed to one record.
        This is what gets pushed to `cleanroom_enrichment_sources__c`."""
        sources = []
        for r in self._rows:
            if r.record_id == record_id and r.source not in sources:
                sources.append(r.source)
        return "|".join(sources)

    def confidence_score_for(self, record_id: str) -> float:
        """Average confidence (high=1.0, medium=0.66, low=0.33) for one record."""
        score = {"high": 1.0, "medium": 0.66, "low": 0.33}
        rows = [r for r in self._rows if r.record_id == record_id]
        if not rows:
            return 0.0
        return round(sum(score.get(r.confidence, 0.0) for r in rows) / len(rows), 2)
