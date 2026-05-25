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
from cleanroom.audit._issue import Issue
from cleanroom.enrichment import run_enrichment, write_enrichment_outputs
from cleanroom.resolution import run_resolution, write_resolution_outputs

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _load_issues(path: Path) -> list[Issue]:
    """Re-hydrate audit issues from issues.jsonl."""
    import json
    issues: list[Issue] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        issues.append(Issue(**d))
    return issues


def _read_accounts_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False).copy()
    for col in ("annual_revenue", "employee_count", "founded_year"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


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
    accounts = _read_accounts_csv(accounts_csv)
    contacts = pd.read_csv(contacts_csv, dtype=str, keep_default_na=False).copy()
    result = run_audit(accounts, contacts)
    write_audit_outputs(result, out)
    _print_audit_summary(result, out)

    if result.has_critical:
        console.print(f"[red]✗ critical issues present ({result.counts_by_severity.get('high', 0)} 'high' severity)[/red]")
        sys.exit(1)
    console.print("[green]✓ no critical issues[/green]")


@app.command()
def resolve(
    accounts_csv: Path = typer.Argument(..., exists=True),
    issues_jsonl: Path = typer.Argument(..., exists=True, help="issues.jsonl from the audit stage"),
    out: Path = typer.Option(Path("data/audit"), "--out"),
    live: bool = typer.Option(False, "--live", help="Use real Claude Haiku for tie-break (needs ANTHROPIC_API_KEY)"),
    cap: int = typer.Option(None, "--cap", help="Override LLM_TIEBREAK_CAP (default 500)"),
):
    """Run LLM tie-break on gray-zone duplicate pairs + apply merges."""
    accounts = _read_accounts_csv(accounts_csv)
    issues = _load_issues(issues_jsonl)
    resolved, plan, decisions, telemetry, _ = run_resolution(accounts, issues, live=live, cap=cap)
    write_resolution_outputs(out, resolved, plan, decisions, telemetry)

    console.print(f"[cyan]→ tie-break mode:[/cyan] {telemetry['mode']}")
    console.print(f"[cyan]→ gray-zone pairs scored:[/cyan] {telemetry['n_gray_zone_pairs']}")
    if telemetry["cap_hit"]:
        console.print(f"[yellow]⚠ cap of {telemetry['cap']} hit — remaining pairs sampled out[/yellow]")
    console.print(f"[cyan]→ same-entity decisions:[/cyan] {telemetry['n_same_entity_true']}")
    console.print(f"[cyan]→ merge groups formed:[/cyan] {telemetry['n_merge_groups']}")
    console.print(f"[green]✓ {len(accounts)} → {len(resolved)} accounts after merge[/green]")
    console.print(f"[dim]→ wrote {out / 'accounts_resolved.csv'}, merge_plan.json, decisions_log.jsonl[/dim]")


@app.command()
def enrich(
    accounts_csv: Path = typer.Argument(..., exists=True, help="Use accounts_resolved.csv from `resolve`"),
    out: Path = typer.Option(Path("data/enrichment"), "--out"),
    cache: Path = typer.Option(Path("data/enrichment/cache.json"), "--cache"),
    live: bool = typer.Option(False, "--live", help="Real Apollo + real Claude (needs APOLLO_API_KEY + ANTHROPIC_API_KEY)"),
):
    """Fill blank fields via Apollo → mock_clearbit → claude_websearch waterfall."""
    accounts = _read_accounts_csv(accounts_csv)
    enriched, tracker = run_enrichment(accounts, live=live, cache_path=cache)
    write_enrichment_outputs(out, enriched, tracker)

    by_source = tracker.by_source()
    by_field = tracker.by_field()
    by_conf = tracker.by_confidence()
    console.print(f"[cyan]→ fields filled:[/cyan] {len(tracker)}")
    console.print(f"[cyan]→ by source:[/cyan] {by_source}")
    console.print(f"[cyan]→ by field:[/cyan] {by_field}")
    console.print(f"[cyan]→ by confidence:[/cyan] {by_conf}")
    console.print(f"[dim]→ wrote {out / 'accounts_enriched.csv'} + field_metadata.jsonl[/dim]")


@app.command()
def push(
    accounts_csv: Path = typer.Argument(..., exists=True, help="Use accounts_enriched.csv from `enrich`"),
    merge_plan_json: Path = typer.Argument(..., exists=True, help="merge_plan.json from `resolve`"),
    metadata_jsonl: Path = typer.Argument(..., exists=True, help="field_metadata.jsonl from `enrich`"),
    out: Path = typer.Option(Path("data/push"), "--out"),
    commit: bool = typer.Option(False, "--commit", help="Actually push (else dry-run + manifest only)"),
):
    """Push cleaned canonical accounts to a Salesforce dev org via Bulk API.

    Dry-run by default — writes data/push/push_manifest.jsonl with the records
    that would have been upserted. Pass --commit to hit the SF Bulk API."""
    import json as _json

    from cleanroom.enrichment.confidence_tracker import ConfidenceTracker, FieldMetadata
    from cleanroom.push.salesforce_upsert import push_accounts

    accounts = _read_accounts_csv(accounts_csv)
    plan_data = _json.loads(merge_plan_json.read_text())
    canonical_map: dict[str, str] = {}
    for entry in plan_data.get("plan", []):
        for merged_id in entry["merged_ids"]:
            canonical_map[merged_id] = entry["canonical_id"]
        canonical_map[entry["canonical_id"]] = entry["canonical_id"]

    # Re-hydrate ConfidenceTracker from field_metadata.jsonl
    tracker = ConfidenceTracker()
    for line in metadata_jsonl.read_text().splitlines():
        if not line.strip():
            continue
        d = _json.loads(line)
        tracker._rows.append(FieldMetadata(**d))

    result = push_accounts(accounts, canonical_map, tracker, out, commit=commit)
    console.print(f"[cyan]→ push mode:[/cyan] {result.mode}")
    console.print(f"[cyan]→ records planned:[/cyan] {result.n_records_planned}")
    if result.mode == "dry_run":
        console.print(f"[dim]→ manifest at {result.manifest_path}[/dim]")
    else:
        console.print(f"[green]→ pushed: {result.n_records_pushed}[/green]")
        if result.n_errors:
            console.print(f"[red]→ errors: {result.n_errors}[/red]")
            for err in result.error_samples:
                console.print(f"  {err}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
