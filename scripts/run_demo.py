"""End-to-end demo runner — what a recruiter sees.

    python scripts/run_demo.py                  # 1000-account default
    python scripts/run_demo.py --size 200       # tiny smoke run
    python scripts/run_demo.py --live           # real Apollo + real Claude (needs keys)
    python scripts/run_demo.py --no-open        # don't auto-open the report

Runs: seed (existing or regenerated) → audit → resolve → enrich →
       re-audit (for after-numbers) → push (dry-run) → HTML report.
Each stage prints its timing. Total target: <60s on the 1000-account default.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console

# Make package importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cleanroom.audit import run_audit, write_audit_outputs  # noqa: E402
from cleanroom.enrichment import run_enrichment, write_enrichment_outputs  # noqa: E402
from cleanroom.push.salesforce_upsert import push_accounts  # noqa: E402
from cleanroom.report.html_renderer import (  # noqa: E402
    ReportInputs,
    StageTiming,
    render_report,
)
from cleanroom.resolution import run_resolution, write_resolution_outputs  # noqa: E402

app = typer.Typer(add_completion=False, no_args_is_help=False)
console = Console()


def _read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False).copy()
    for col in ("annual_revenue", "employee_count", "founded_year"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@app.command()
def main(
    size: int = typer.Option(1000, "--size"),
    seed_dir: Path = typer.Option(Path("data/seed"), "--seed-dir"),
    audit_dir: Path = typer.Option(Path("data/audit"), "--audit-dir"),
    enrich_dir: Path = typer.Option(Path("data/enrichment"), "--enrich-dir"),
    push_dir: Path = typer.Option(Path("data/push"), "--push-dir"),
    report_path: Path = typer.Option(Path("reports/before_after.html"), "--report"),
    live: bool = typer.Option(False, "--live", help="Use real APIs (needs APOLLO + ANTHROPIC keys)"),
    commit: bool = typer.Option(False, "--commit", help="Actually push to Salesforce (needs SF_* keys)"),
    regenerate_seed: bool = typer.Option(False, "--regenerate-seed", help="Regenerate seed CSVs"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Auto-open the HTML report"),
):
    """End-to-end demo runner. Prints stage timing and opens the report."""
    mode = "live" if live else "dry-run"
    console.rule(f"[bold]Cleanroom demo — {mode} mode[/bold]")

    timings: list[StageTiming] = []

    # --- Stage 0: seed ------------------------------------------------------
    t0 = time.perf_counter()
    accounts_csv = seed_dir / "accounts.csv"
    contacts_csv = seed_dir / "contacts.csv"
    if regenerate_seed or not accounts_csv.exists() or not contacts_csv.exists():
        console.print(f"→ generating fresh seed ({size} accounts)…")
        subprocess.run(
            [sys.executable, "scripts/generate_seed.py", "--size", str(size), "--out", str(seed_dir)],
            check=True,
        )
    seed_secs = time.perf_counter() - t0
    timings.append(StageTiming("seed (load or regenerate)", seed_secs))

    accounts_before = _read_csv(accounts_csv)
    contacts_before = _read_csv(contacts_csv)
    console.print(f"   loaded {len(accounts_before)} accounts × {len(contacts_before)} contacts")

    # --- Stage 1: audit (before) -------------------------------------------
    t = time.perf_counter()
    audit_before = run_audit(accounts_before, contacts_before)
    write_audit_outputs(audit_before, audit_dir)
    timings.append(StageTiming("audit (before)", time.perf_counter() - t))
    console.print(f"   {len(audit_before.issues)} issues "
                  f"({audit_before.counts_by_severity.get('high', 0)} high, "
                  f"{audit_before.counts_by_severity.get('medium', 0)} medium, "
                  f"{audit_before.counts_by_severity.get('low', 0)} low)")

    # --- Stage 2: resolve --------------------------------------------------
    t = time.perf_counter()
    resolved, merge_plan, decisions, telemetry, canonical_map = run_resolution(
        accounts_before, audit_before.issues, live=live
    )
    write_resolution_outputs(audit_dir, resolved, merge_plan, decisions, telemetry)
    timings.append(StageTiming("resolve", time.perf_counter() - t))
    n_llm_resolved = telemetry["n_same_entity_true"]
    console.print(f"   {len(accounts_before)} → {len(resolved)} accounts "
                  f"({len(merge_plan)} merge groups, {n_llm_resolved} LLM-confirmed)")

    # --- Stage 3: enrich ---------------------------------------------------
    t = time.perf_counter()
    enriched, tracker = run_enrichment(resolved, live=live, cache_path=enrich_dir / "cache.json")
    write_enrichment_outputs(enrich_dir, enriched, tracker)
    timings.append(StageTiming("enrich (3-provider waterfall)", time.perf_counter() - t))
    console.print(f"   filled {len(tracker)} fields "
                  f"(apollo: {tracker.by_source().get('apollo', 0)}, "
                  f"mock_clearbit: {tracker.by_source().get('mock_clearbit', 0)}, "
                  f"claude_websearch: {tracker.by_source().get('claude_websearch', 0)})")

    # --- Stage 4: re-audit (after) -----------------------------------------
    t = time.perf_counter()
    # Contacts unchanged in this version of the pipeline.
    audit_after = run_audit(enriched, contacts_before)
    timings.append(StageTiming("audit (after)", time.perf_counter() - t))
    console.print(f"   after-state: {len(audit_after.issues)} issues "
                  f"({audit_after.counts_by_severity.get('high', 0)} high)")

    # --- Stage 5: push (dry-run unless --commit) ---------------------------
    t = time.perf_counter()
    push_result = push_accounts(enriched, canonical_map, tracker, push_dir, commit=commit)
    timings.append(StageTiming("push (dry-run)" if push_result.mode == "dry_run" else "push (committed)",
                                time.perf_counter() - t))
    if push_result.mode == "dry_run":
        console.print(f"   would push {push_result.n_records_planned} canonical accounts "
                      f"(manifest at {push_result.manifest_path})")
    else:
        console.print(f"   pushed {push_result.n_records_pushed}/{push_result.n_records_planned} "
                      f"({push_result.n_errors} errors)")

    # --- Stage 6: render report -------------------------------------------
    t = time.perf_counter()
    inputs = ReportInputs(
        seed_csv_path=str(accounts_csv),
        mode=mode,
        audit_before=audit_before,
        audit_after=audit_after,
        n_accounts_before=len(accounts_before),
        n_accounts_after=len(resolved),
        n_contacts_before=len(contacts_before),
        n_llm_resolved=n_llm_resolved,
        merge_plan=merge_plan,
        tracker=tracker,
        stage_timing=timings,
    )
    out = render_report(inputs, accounts_before, report_path, live=live, open_browser=open_browser)
    timings.append(StageTiming("render report", time.perf_counter() - t))
    console.print(f"   → {out}")

    total = sum(t.seconds for t in timings)
    console.rule(f"[bold green]done — {total:.1f}s total[/bold green]")


if __name__ == "__main__":
    app()
