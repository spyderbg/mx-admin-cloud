"""Write per-run JSON and CSV reports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..models import RunSummary
from ..output_paths import reports_dir


def write_run_summary(base_dir: Path, summary: RunSummary) -> tuple[Path, Path]:
    """Write the run summary to a JSON and CSV file."""
    report_dir = reports_dir(base_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    json_path = report_dir / f"{summary.run_id}.json"
    csv_path = report_dir / f"{summary.run_id}.csv"

    json_path.write_text(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    fieldnames = (
        list[str](summary.provider_records[0].to_dict().keys())
        if summary.provider_records
        else ["run_id"]
    )
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        if summary.provider_records:
            for record in summary.provider_records:
                writer.writerow(record.to_dict())
        else:
            writer.writerow({"run_id": summary.run_id})

    return json_path, csv_path
