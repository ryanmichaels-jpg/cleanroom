"""CLI wrapper around the seed generator.

Usage:
    python scripts/generate_seed.py                       # 1000 accounts, ~3000 contacts
    python scripts/generate_seed.py --size 5000           # benchmark scale
    python scripts/generate_seed.py --size 200 --seed 7   # tiny + custom seed
    python scripts/generate_seed.py --out data/scratch    # alternate output dir

Output:
    data/seed/accounts.csv
    data/seed/contacts.csv
    data/seed/seed_metadata.json  (record counts + the config + seed used)
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import typer
import yaml

# Make the package importable when run directly (`python scripts/generate_seed.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cleanroom.seed.flaw_injector import inject_all_flaws  # noqa: E402
from cleanroom.seed.generator import (  # noqa: E402
    GenContext,
    generate_clean_accounts,
    generate_clean_contacts,
)

app = typer.Typer(add_completion=False, no_args_is_help=False)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "src" / "cleanroom" / "seed" / "seed_config.yaml"


@app.command()
def main(
    size: int = typer.Option(None, "--size", help="Number of accounts to generate. Default = config.dataset.default_accounts"),
    seed: int = typer.Option(None, "--seed", help="Override config.random_seed for this run"),
    out: Path = typer.Option(Path("data/seed"), "--out", help="Output directory"),
    config_path: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config", help="Path to seed_config.yaml"),
):
    """Generate a deterministically messy CRM dataset."""
    config = yaml.safe_load(config_path.read_text())
    n_accounts = size if size is not None else config["dataset"]["default_accounts"]
    rseed = seed if seed is not None else config["random_seed"]

    typer.echo(f"→ generating {n_accounts} accounts with seed={rseed}")
    ctx = GenContext.from_seed(rseed)
    rng = random.Random(rseed + 1)  # separate stream for flaw injection

    accounts = generate_clean_accounts(n_accounts, ctx, hero_dupes=config.get("hero_dupes"))
    contacts = generate_clean_contacts(
        accounts,
        ctx,
        mean_per_account=config["dataset"]["default_contacts_per_account_mean"],
        max_per_account=config["dataset"]["default_contacts_per_account_max"],
    )

    typer.echo(f"→ clean: {len(accounts)} accounts, {len(contacts)} contacts")
    accounts, contacts = inject_all_flaws(accounts, contacts, config, rng)
    typer.echo(f"→ messy: {len(accounts)} accounts, {len(contacts)} contacts")

    out.mkdir(parents=True, exist_ok=True)
    accounts_path = out / "accounts.csv"
    contacts_path = out / "contacts.csv"
    accounts.to_csv(accounts_path, index=False)
    contacts.to_csv(contacts_path, index=False)
    meta = {
        "config_path": str(config_path),
        "seed": rseed,
        "n_accounts": int(len(accounts)),
        "n_contacts": int(len(contacts)),
        "n_unique_account_names": int(accounts["name"].nunique()),
        "n_unique_domains": int(accounts["domain"].nunique()),
    }
    (out / "seed_metadata.json").write_text(json.dumps(meta, indent=2))

    typer.echo(f"✓ wrote {accounts_path}")
    typer.echo(f"✓ wrote {contacts_path}")
    typer.echo(f"✓ wrote {out / 'seed_metadata.json'}")


if __name__ == "__main__":
    app()
