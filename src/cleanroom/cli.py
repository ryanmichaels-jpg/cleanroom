"""Top-level Cleanroom CLI.

    python -m cleanroom audit data/seed/accounts.csv data/seed/contacts.csv --out data/audit/
    python -m cleanroom resolve  ...    (stub, Phase 4)
    python -m cleanroom enrich   ...    (stub, Phase 4)
    python -m cleanroom push     ...    (stub, Phase 5)

Phase-by-phase plumbing: each subcommand exists from day one. Unimplemented
ones raise NotImplementedError with the phase number so a reader knows the roadmap.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from cleanroom.audit import run_audit, write_audit_outputs

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _print_audit_summary(result, output_path: Path) -> None:
    table = Table(title=f"Audit summary — {result.n_accounts} accounts, {result.n_contacts} contacts")
    table.add_column("issue_type", style="cyan")
    table.add_column("count", justify="right")
    table.add_column("severity_high", justify="right", style="red")
    table.add_column("severity_med", justify="right", style="yellow")
    table.add_column("severity_low", justify="right", style="green")

    # Pivot: issue_type × severity
    by_type_sev: dict[str, Counter] = {}
    for issue in result.issues:
        by_type_sev.setdefault(issue.issue_type, Counter())[issue.severity] += 1

    for itype, sevs in sorted(by_type_sev.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(sevs.values())
        table.add_row(itype, str(total), str(sevs.get("high", 0)), str(sevs.get("medium", 0)), str(sevs.get("low", 0)))
    table.add_row("", "", "", "", "", style="dim")
    table.add_row(
        "TOTAL",
        str(len(result.issues)),
        str(result.counts_by_severity.get("high", 0)),
        str(result.counts_by_severity.get("medium", 0)),
        str(result.counts_by_severity.get("low", 0)),
        style="bold",
    )
    console.print(table)
    console.print(f"[dim]→ wrote {output_path / 'issues.jsonl'}[/dim]")
    console.print(f"[dim]→ wrote {output_path / 'summary.json'}[/dim]")


@app.command()
def audit(
    accounts_csv: Path = typer.Argument(..., exists=True, help="Accounts CSV from the seed or a real export"),
    contacts_csv: Path = typer.Argument(..., exists=True, help="Contacts CSV"),
    out: Path = typer.Option(Path("data/audit"), "--out", help="Where issues.jsonl + summary.json land"),
):
    """Non-destructive audit: dupes, schema, completeness, orphans, lifecycle."""
    accounts = pd.read_csv(accounts_csv, dtype=str, keep_default_na=False).copy()
    contacts = pd.read_csv(contacts_csv, dtype=str, keep_default_na=False).copy()

    # Re-cast numeric-looking fields back to numeric so validators behave.
    for col in ("annual_revenue", "employee_count", "founded_year"):
        if col in accounts.columns:
            accounts[col] = pd.to_numeric(accounts[col], errors="coerce")

    result = run_audit(accounts, contacts)
    write_audit_outputs(result, out)
    _print_audit_summary(result, out)

    if result.has_critical:
        console.print(f"[red]✗ critical issues present ({result.counts_by_severity.get('high', 0)} 'high' severity)[/red]")
        sys.exit(1)
    console.print("[green]✓ no critical issues[/green]")


@app.command()
def resolve():
    """Resolve dedup gray-zone pairs via LLM tie-breaker. (Phase 4 — coming.)"""
    raise typer.Exit(code=2) from NotImplementedError("resolve lands in Phase 4")


@app.command()
def enrich():
    """Fill missing fields via Apollo → mocks → Claude waterfall. (Phase 4 — coming.)"""
    raise typer.Exit(code=2) from NotImplementedError("enrich lands in Phase 4")


@app.command()
def push():
    """Push cleaned records to a Salesforce dev org. (Phase 5 — coming.)"""
    raise typer.Exit(code=2) from NotImplementedError("push lands in Phase 5")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
