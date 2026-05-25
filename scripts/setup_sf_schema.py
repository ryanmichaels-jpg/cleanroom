"""Create the four cleanroom_*__c custom fields on Salesforce Account
via the Metadata API.

Run ONCE per Salesforce dev org, before any `cleanroom push --commit`.

    python scripts/setup_sf_schema.py        # requires SF_* env vars

Custom fields created on Account:
  - External_Id__c               (Text 64, unique, External ID — for upsert)
  - cleanroom_audit_date__c       (Date)
  - cleanroom_confidence_score__c (Number 4,2)
  - cleanroom_dedup_canonical_id__c (Text 32)
  - cleanroom_enrichment_sources__c (Text 255)

Note: Salesforce object metadata is also exposed in Setup → Object Manager →
Account → Fields & Relationships if you'd rather click through. This script
is here so the demo doesn't depend on manual UI steps.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _need(env: str) -> str:
    v = os.getenv(env)
    if not v:
        print(f"✗ {env} not set in .env", file=sys.stderr)
        sys.exit(1)
    return v


_FIELDS = [
    {
        "fullName": "Account.External_Id__c",
        "label": "External Id (Cleanroom)",
        "type": "Text",
        "length": 64,
        "unique": True,
        "externalId": True,
    },
    {
        "fullName": "Account.cleanroom_audit_date__c",
        "label": "Cleanroom Audit Date",
        "type": "Date",
    },
    {
        "fullName": "Account.cleanroom_confidence_score__c",
        "label": "Cleanroom Confidence Score",
        "type": "Number",
        "precision": 4,
        "scale": 2,
    },
    {
        "fullName": "Account.cleanroom_dedup_canonical_id__c",
        "label": "Cleanroom Dedup Canonical Id",
        "type": "Text",
        "length": 32,
    },
    {
        "fullName": "Account.cleanroom_enrichment_sources__c",
        "label": "Cleanroom Enrichment Sources",
        "type": "Text",
        "length": 255,
    },
]


def main() -> None:
    try:
        from simple_salesforce import Salesforce
    except ImportError:
        print("✗ simple-salesforce not installed; run ./scripts/setup.sh first", file=sys.stderr)
        sys.exit(1)

    sf = Salesforce(
        username=_need("SF_USERNAME"),
        password=_need("SF_PASSWORD"),
        security_token=_need("SF_SECURITY_TOKEN"),
        domain=os.getenv("SF_DOMAIN", "login"),
    )

    print(f"→ connected to {sf.sf_instance}")
    mdapi = sf.mdapi
    for spec in _FIELDS:
        try:
            result = mdapi.CustomField.create(spec)
            print(f"✓ {spec['fullName']}: {result}")
        except Exception as e:
            # Most common error: field already exists. Surface but don't abort.
            print(f"  {spec['fullName']}: {e}")

    print()
    print("✓ schema setup complete. Now: cleanroom push --commit")


if __name__ == "__main__":
    main()
