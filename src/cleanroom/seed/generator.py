"""Faker-based clean-data generator for accounts + contacts.

Produces deterministic clean records given a seed. The flaw_injector
mutates these afterwards to look like a real CRM.

Borrowed pattern: gooseworks-ai/goose-skills `contact-cache` uses
LinkedIn-URL + email as the dedup key. We use account.domain +
contact.email here for the same reason — stable across re-runs.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from faker import Faker

# --- Domain vocabulary -------------------------------------------------------

INDUSTRIES = [
    "Software",
    "Financial Services",
    "Healthcare",
    "Manufacturing",
    "Retail",
    "Media",
    "Education",
    "Real Estate",
    "Energy",
    "Telecommunications",
    "Consumer Goods",
    "Logistics",
    "Hospitality",
    "Professional Services",
    "Construction",
    "Automotive",
    "Biotechnology",
    "Insurance",
]

LIFECYCLE_STAGES_ACCOUNT = [
    "Lead",
    "MQL",
    "SQL",
    "Opportunity",
    "Customer",
    "Closed Lost",
]
LIFECYCLE_STAGE_ACCOUNT_WEIGHTS = [0.30, 0.25, 0.15, 0.12, 0.10, 0.08]

LIFECYCLE_STAGES_CONTACT = ["Subscriber", "Lead", "MQL", "SQL", "Customer", "Other"]
LIFECYCLE_STAGE_CONTACT_WEIGHTS = [0.20, 0.30, 0.20, 0.15, 0.10, 0.05]

TITLES = [
    "VP of Sales",
    "Director of Marketing",
    "Account Executive",
    "Sales Development Rep",
    "Chief Revenue Officer",
    "Chief Marketing Officer",
    "Head of Growth",
    "Marketing Operations Manager",
    "Sales Operations Manager",
    "Revenue Operations Lead",
    "Demand Gen Manager",
    "Customer Success Manager",
    "VP of Marketing",
    "Director of Sales",
    "Product Marketing Manager",
]

# Real US state codes. Schema violations swap these to "ZZ" later.
US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]

# Active + inactive owner pool. Inactive owners are injected by flaw_injector
# onto a configured % of records.
ACTIVE_OWNER_IDS = [f"owner_{i:03d}" for i in range(1, 21)]      # owner_001 .. owner_020
INACTIVE_OWNER_IDS = [f"inactive_user_{i:02d}" for i in range(1, 6)]  # inactive_user_01..05

# --- Account columns (Salesforce-ish, but our own naming) --------------------

ACCOUNT_COLUMNS = [
    "id",
    "name",
    "domain",
    "industry",
    "annual_revenue",
    "employee_count",
    "founded_year",
    "country",
    "state",
    "city",
    "phone",
    "owner_id",
    "lifecycle_stage",
    "created_at",
    "updated_at",
]

CONTACT_COLUMNS = [
    "id",
    "first_name",
    "last_name",
    "email",
    "phone",
    "title",
    "account_id",
    "owner_id",
    "lifecycle_stage",
    "created_at",
    "updated_at",
]


# --- Helpers -----------------------------------------------------------------


@dataclass
class GenContext:
    """Bundled RNG + Faker, both seeded, so every random pick is reproducible."""

    rng: random.Random
    fake: Faker
    now: datetime

    @classmethod
    def from_seed(cls, seed: int) -> "GenContext":
        rng = random.Random(seed)
        Faker.seed(seed)
        fake = Faker("en_US")
        # Anchor "now" to a fixed point so created_at/updated_at are reproducible.
        now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
        return cls(rng=rng, fake=fake, now=now)


def _e164_phone(rng: random.Random) -> str:
    """Generate an E.164 US phone like +15551234567."""
    area = rng.randint(200, 989)
    prefix = rng.randint(200, 989)
    line = rng.randint(0, 9999)
    return f"+1{area}{prefix}{line:04d}"


def _company_domain(name: str, rng: random.Random) -> str:
    """Derive a plausible domain from a company name."""
    slug = "".join(ch.lower() for ch in name if ch.isalnum())
    tld = rng.choices(["com", "io", "co", "ai"], weights=[0.70, 0.15, 0.10, 0.05])[0]
    return f"{slug}.{tld}"


def _stable_id(prefix: str, idx: int) -> str:
    """Stable string ID. We use UUID4 in places where it matters, but for the
    bulk of the data we want predictable IDs so downstream tests are readable."""
    return f"{prefix}_{idx:06d}"


# --- Account generation ------------------------------------------------------


def _generate_one_account(ctx: GenContext, idx: int) -> dict[str, Any]:
    fake, rng, now = ctx.fake, ctx.rng, ctx.now
    name = fake.company()
    domain = _company_domain(name, rng)
    founded_year = rng.randint(1960, 2024)
    created_offset = timedelta(days=rng.randint(30, 2000))
    created_at = now - created_offset
    updated_at = created_at + timedelta(days=rng.randint(0, max(1, created_offset.days)))

    return {
        "id": _stable_id("acct", idx),
        "name": name,
        "domain": domain,
        "industry": rng.choice(INDUSTRIES),
        "annual_revenue": round(rng.uniform(500_000, 500_000_000), 2),
        "employee_count": rng.choice([10, 25, 50, 100, 250, 500, 1000, 2500, 5000]),
        "founded_year": founded_year,
        "country": "US",
        "state": rng.choice(US_STATES),
        "city": fake.city(),
        "phone": _e164_phone(rng),
        "owner_id": rng.choice(ACTIVE_OWNER_IDS),
        "lifecycle_stage": rng.choices(
            LIFECYCLE_STAGES_ACCOUNT, weights=LIFECYCLE_STAGE_ACCOUNT_WEIGHTS
        )[0],
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def _generate_hero_account(
    ctx: GenContext, idx: int, name: str, domain: str, industry: str
) -> dict[str, Any]:
    """Hero dupes are partly hand-crafted so the demo CSV shows obvious variants
    in the first 20 rows. We still vary state/city/owner/etc."""
    rng, now = ctx.rng, ctx.now
    fake = ctx.fake
    created_offset = timedelta(days=rng.randint(60, 1500))
    created_at = now - created_offset
    updated_at = created_at + timedelta(days=rng.randint(0, created_offset.days))
    return {
        "id": _stable_id("acct", idx),
        "name": name,
        "domain": domain,
        "industry": industry,
        "annual_revenue": round(rng.uniform(5_000_000, 200_000_000), 2),
        "employee_count": rng.choice([100, 250, 500, 1000, 2500]),
        "founded_year": rng.randint(1980, 2015),
        "country": "US",
        "state": rng.choice(US_STATES),
        "city": fake.city(),
        "phone": _e164_phone(rng),
        "owner_id": rng.choice(ACTIVE_OWNER_IDS),
        "lifecycle_stage": rng.choices(
            LIFECYCLE_STAGES_ACCOUNT, weights=LIFECYCLE_STAGE_ACCOUNT_WEIGHTS
        )[0],
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def generate_clean_accounts(
    n: int,
    ctx: GenContext,
    hero_dupes: list[dict] | None = None,
) -> pd.DataFrame:
    """Generate n clean accounts. If hero_dupes is provided, the first len(hero_dupes)
    expanded rows are the hero variants — committed at predictable low IDs so the
    Loom demo's `head -20` always shows them.
    """
    rows: list[dict[str, Any]] = []
    idx = 1

    # Hero dupes first (each canonical + each variant gets its own row).
    if hero_dupes:
        for group in hero_dupes:
            rows.append(
                _generate_hero_account(
                    ctx, idx, group["canonical_name"], group["canonical_domain"], group["industry"]
                )
            )
            idx += 1
            for variant in group.get("variants", []):
                rows.append(
                    _generate_hero_account(
                        ctx, idx, variant["name"], variant["domain"], group["industry"]
                    )
                )
                idx += 1

    while idx <= n:
        rows.append(_generate_one_account(ctx, idx))
        idx += 1

    df = pd.DataFrame(rows, columns=ACCOUNT_COLUMNS)
    return df


# --- Contact generation ------------------------------------------------------


def _generate_one_contact(
    ctx: GenContext,
    idx: int,
    account_row: pd.Series,
) -> dict[str, Any]:
    fake, rng, now = ctx.fake, ctx.rng, ctx.now
    first = fake.first_name()
    last = fake.last_name()
    domain = account_row["domain"]
    email = f"{first.lower()}.{last.lower()}@{domain}"

    created_offset = timedelta(days=rng.randint(15, 1500))
    created_at = now - created_offset
    updated_at = created_at + timedelta(days=rng.randint(0, max(1, created_offset.days)))

    return {
        "id": _stable_id("cont", idx),
        "first_name": first,
        "last_name": last,
        "email": email,
        "phone": _e164_phone(rng),
        "title": rng.choice(TITLES),
        "account_id": account_row["id"],
        "owner_id": account_row["owner_id"],
        "lifecycle_stage": rng.choices(
            LIFECYCLE_STAGES_CONTACT, weights=LIFECYCLE_STAGE_CONTACT_WEIGHTS
        )[0],
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


def generate_clean_contacts(
    accounts: pd.DataFrame,
    ctx: GenContext,
    mean_per_account: int = 3,
    max_per_account: int = 8,
) -> pd.DataFrame:
    """Generate ~mean_per_account contacts per account, capped at max_per_account.
    Uses a geometric-ish distribution so most accounts have 1–4 contacts and a
    long tail have 5–8."""
    rng = ctx.rng
    rows: list[dict[str, Any]] = []
    idx = 1
    for _, account_row in accounts.iterrows():
        # Geometric draw centered on mean_per_account.
        n_contacts = max(1, min(max_per_account, int(rng.gauss(mean_per_account, 1.5))))
        for _ in range(n_contacts):
            rows.append(_generate_one_contact(ctx, idx, account_row))
            idx += 1
    df = pd.DataFrame(rows, columns=CONTACT_COLUMNS)
    return df
