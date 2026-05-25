"""Find duplicate accounts and contacts via rapidfuzz blocking + scoring.

Blocking strategy (accounts):
  1. Normalize name (lowercase, strip suffixes like Corp/Inc/LLC, strip punct).
  2. Block by (a) first letter of normalized name AND (b) domain root
     (prefix-stripped + tld-stripped). Union both candidate sets.
  3. Score every candidate pair with rapidfuzz.fuzz.token_sort_ratio.

Severity mapping per pair:
  - score >= 90  →  high     (almost certainly a dup; auto-merge candidate)
  - score 70–89  →  medium   (gray zone; goes to the LLM tie-breaker in Phase 4)
  - score < 70   →  not emitted (precision over recall)

Borrowed pattern: gooseworks-ai/goose-skills `contact-cache` keys on
LinkedIn-URL preferred, email fallback. Same idea here — block on the
strongest signal (domain root) first, name normalization second.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterator

import pandas as pd
from rapidfuzz import fuzz

from ._issue import Issue

# --- Normalization helpers ---------------------------------------------------

_LEGAL_SUFFIXES = (
    "corporation", "incorporated", "limited", "inc", "llc", "corp",
    "co", "ltd", "lp", "plc", "ag", "sa", "gmbh",
)
_DOMAIN_PREFIXES = ("get", "try", "use", "the", "my")


def normalize_name(name: str) -> str:
    """Lowercase + strip punct + strip trailing legal suffix + collapse whitespace."""
    if not isinstance(name, str):
        return ""
    s = name.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip one trailing legal suffix token
    tokens = s.split()
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def domain_root(domain: str) -> str:
    """Drop tld + drop a leading "get/try/use/the/my" prefix.
    'acme.com' → 'acme', 'getacme.io' → 'acme', 'globex.com' → 'globex'.
    """
    if not isinstance(domain, str) or "." not in domain:
        return ""
    base = domain.lower().rsplit(".", 1)[0]
    for prefix in _DOMAIN_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix) + 2:
            base = base[len(prefix):]
            break
    return base


def _severity_for_score(score: float) -> str | None:
    if score >= 90:
        return "high"
    if score >= 70:
        return "medium"
    return None


# --- Blocking ----------------------------------------------------------------


def _build_blocks(accounts: pd.DataFrame) -> dict[str, list[int]]:
    """Return blocks keyed by either 'name:<letter>' or 'domain:<root>'.
    Each block is a list of row indices. The same row appears in multiple blocks
    when it has both a name and a domain — that's how we get cross-blocking."""
    blocks: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(accounts.itertuples(index=False)):
        n = normalize_name(getattr(row, "name", ""))
        if n:
            blocks[f"name:{n[0]}"].append(i)
        d = domain_root(getattr(row, "domain", ""))
        if d:
            blocks[f"domain:{d}"].append(i)
    return blocks


# --- Account dedup -----------------------------------------------------------


def find_account_duplicates(accounts: pd.DataFrame) -> Iterator[Issue]:
    """Yield one Issue per duplicate-candidate pair (score >= 70).

    Each pair generates ONE issue, on the row with the higher index, pointing
    back to the lower-indexed row as `matched_with`. This keeps issues.jsonl
    pair-counted once, not twice.
    """
    if accounts.empty:
        return
    blocks = _build_blocks(accounts)
    seen_pairs: set[tuple[int, int]] = set()

    names = accounts["name"].astype(str).tolist()
    domains = accounts["domain"].astype(str).tolist()
    ids = accounts["id"].astype(str).tolist()

    for block_key, indices in blocks.items():
        if len(indices) < 2:
            continue
        # All pairs in this block
        for i in range(len(indices)):
            for j in range(i + 1, len(indices)):
                a, b = indices[i], indices[j]
                if a == b:
                    continue
                pair = (min(a, b), max(a, b))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                # Score on normalized names. We boost when domain roots match too.
                na, nb = normalize_name(names[a]), normalize_name(names[b])
                name_score = fuzz.token_sort_ratio(na, nb) if na and nb else 0
                same_domain_root = (
                    domain_root(domains[a]) and domain_root(domains[a]) == domain_root(domains[b])
                )
                score = name_score + (10 if same_domain_root else 0)
                score = min(100.0, float(score))

                sev = _severity_for_score(score)
                if sev is None:
                    continue
                yield Issue(
                    record_id=ids[pair[1]],
                    record_type="account",
                    issue_type="duplicate_candidate",
                    severity=sev,
                    field=None,
                    detail={
                        "matched_with": ids[pair[0]],
                        "score": round(score, 1),
                        "name_score": round(float(name_score), 1),
                        "same_domain_root": bool(same_domain_root),
                        "block_key": block_key,
                        "matched_name": names[pair[0]],
                        "this_name": names[pair[1]],
                    },
                )


# --- Contact dedup -----------------------------------------------------------


def _normalize_email(email: str) -> str:
    if not isinstance(email, str):
        return ""
    return email.strip().lower()


def find_contact_duplicates(contacts: pd.DataFrame) -> Iterator[Issue]:
    """Two passes:
      1. Exact normalized-email match → high severity dup.
      2. Same account_id + token_sort_ratio(full_name) >= 85 → medium.
    """
    if contacts.empty:
        return

    # Pass 1: exact email
    by_email: dict[str, list[int]] = defaultdict(list)
    for i, email in enumerate(contacts["email"].astype(str).tolist()):
        ne = _normalize_email(email)
        if ne and "@" in ne:
            by_email[ne].append(i)
    ids = contacts["id"].astype(str).tolist()
    for ne, idxs in by_email.items():
        if len(idxs) < 2:
            continue
        canonical = idxs[0]
        for dup in idxs[1:]:
            yield Issue(
                record_id=ids[dup],
                record_type="contact",
                issue_type="duplicate_candidate",
                severity="high",
                field="email",
                detail={
                    "matched_with": ids[canonical],
                    "score": 100.0,
                    "method": "exact_email",
                    "email": ne,
                },
            )

    # Pass 2: same account_id, fuzzy full name
    if "account_id" not in contacts.columns:
        return
    account_groups: dict[str, list[int]] = defaultdict(list)
    for i, acct in enumerate(contacts["account_id"].astype(str).tolist()):
        if acct:
            account_groups[acct].append(i)

    firsts = contacts["first_name"].astype(str).tolist()
    lasts = contacts["last_name"].astype(str).tolist()
    seen_pairs: set[tuple[int, int]] = set()

    for acct, idxs in account_groups.items():
        if len(idxs) < 2:
            continue
        for i in range(len(idxs)):
            for j in range(i + 1, len(idxs)):
                a, b = idxs[i], idxs[j]
                pair = (min(a, b), max(a, b))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                na = f"{firsts[a]} {lasts[a]}".strip().lower()
                nb = f"{firsts[b]} {lasts[b]}".strip().lower()
                if not na or not nb:
                    continue
                score = fuzz.token_sort_ratio(na, nb)
                if score < 85:
                    continue
                yield Issue(
                    record_id=ids[pair[1]],
                    record_type="contact",
                    issue_type="duplicate_candidate",
                    severity="medium",
                    field=None,
                    detail={
                        "matched_with": ids[pair[0]],
                        "score": round(float(score), 1),
                        "method": "fuzzy_name_same_account",
                        "account_id": acct,
                    },
                )
