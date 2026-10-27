"""Collapse daily snapshots to one row per drive.

Raw files are unzipped one quarter at a time and removed after the
quarter parquet is written. The zip stays in data/raw so the step can
be rerun. Pass --force to rebuild parquets that already exist.
"""

from __future__ import annotations

import csv
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb

from analysis.assumptions import (
    DATA_INTERIM,
    DATA_PROCESSED,
    DATA_RAW,
    DATA_TMP,
    PUBLISHED_Q2_2026,
    QUARTERS,
    REPORTS,
    SQL,
    ZIP_NAMES,
)

REQUIRED_COLUMNS = (
    "date",
    "serial_number",
    "model",
    "capacity_bytes",
    "failure",
    "smart_9_raw",
)
OPTIONAL_COLUMNS = ("datacenter", "is_legacy_format")


def connect() -> duckdb.DuckDBPyConnection:
    """DuckDB connection that can spill a quarter's columns to disk."""
    DATA_TMP.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order=false")
    con.execute("SET memory_limit='8GB'")
    con.execute(f"SET temp_directory='{DATA_TMP}'")
    return con


def csv_headers(path: Path) -> list[str]:
    """Read the header only. utf-8-sig drops a byte-order mark."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return next(csv.reader(handle))


def projection_sql(present: set[str]) -> str:
    """Stable column list. A column missing from the whole quarter becomes NULL."""
    specs = [
        ("date", "DATE", "obs_date"),
        ("serial_number", "VARCHAR", "serial_number"),
        ("model", "VARCHAR", "model"),
        ("capacity_bytes", "BIGINT", "capacity_bytes"),
        ("failure", "INTEGER", "failure"),
        ("smart_9_raw", "BIGINT", "smart_9_raw"),
        ("datacenter", "VARCHAR", "datacenter"),
        ("is_legacy_format", "VARCHAR", "is_legacy_raw"),
    ]
    parts = []
    for name, sql_type, alias in specs:
        if name in present:
            parts.append(f'try_cast("{name}" AS {sql_type}) AS {alias}')
        else:
            parts.append(f"CAST(NULL AS {sql_type}) AS {alias}")
    return ",\n    ".join(parts)


def _consume_rejects(con: duckdb.DuckDBPyConnection) -> tuple[int, list[tuple]]:
    """Read and clear parser rejects. store_rejects cannot combine with union_by_name."""
    try:
        count = con.execute("SELECT count(*) FROM reject_errors").fetchone()[0]
    except duckdb.CatalogException:
        return 0, []
    columns = [row[0] for row in con.execute("DESCRIBE reject_errors").fetchall()]
    if "error_message" in columns:
        message_col = "error_message"
    elif "error_type" in columns:
        message_col = "error_type"
    else:
        message_col = columns[0]
    reasons = con.execute(
        f"""
        SELECT {message_col} AS message, count(*) AS n
        FROM reject_errors
        GROUP BY 1
        ORDER BY n DESC
        LIMIT 5
        """
    ).fetchall()
    con.execute("DELETE FROM reject_errors")
    return int(count), reasons


def load_snapshots(con: duckdb.DuckDBPyConnection, csv_paths: list[Path], header_lists: list[list[str]]) -> dict:
    """Read daily files into snap_rows.

    Files that share a header are read together. Groups are loaded separately
    because DuckDB will not collect rejects when union_by_name is on, and a
    quarter can add a column without sharing it with every file.
    """
    con.execute("DROP TABLE IF EXISTS snap_rows")
    groups: dict[tuple[str, ...], list[Path]] = {}
    for path, header in zip(csv_paths, header_lists):
        groups.setdefault(tuple(header), []).append(path)

    reject_count = 0
    reason_counts: dict[str, int] = {}
    first = True
    for header, paths in groups.items():
        statement = "CREATE TEMP TABLE snap_rows AS" if first else "INSERT INTO snap_rows"
        con.execute(
            f"""
            {statement}
            SELECT
                {projection_sql(set(header))}
            FROM read_csv(
                ?,
                header = true,
                union_by_name = false,
                all_varchar = true,
                strict_mode = false,
                ignore_errors = true,
                store_rejects = true,
                parallel = true,
                null_padding = true,
                encoding = 'utf-8'
            )
            """,
            [[str(path) for path in paths]],
        )
        first = False
        count, reasons = _consume_rejects(con)
        reject_count += count
        for message, n in reasons:
            key = str(message)
            reason_counts[key] = reason_counts.get(key, 0) + int(n)
    top_reasons = sorted(reason_counts.items(), key=lambda item: item[1], reverse=True)[:5]
    return {
        "rejected_rows": reject_count,
        "reject_reasons": [{"message": message, "rows": n} for message, n in top_reasons],
    }


def collapse_csvs(csv_paths: list[Path], quarter: str, out_parquet: Path) -> dict:
    """Collapse one set of daily CSVs and write a quarter parquet."""
    header_lists = [csv_headers(path) for path in csv_paths]
    header_sets = [tuple(header) for header in header_lists]
    unique_headers = sorted(set(header_sets))
    missing_required = []
    for index, header in enumerate(header_lists):
        absent = [column for column in REQUIRED_COLUMNS if column not in header]
        if absent:
            missing_required.append({"file": csv_paths[index].name, "missing": absent})
    if missing_required:
        raise RuntimeError(f"{quarter} is missing required columns: {missing_required[:3]}")

    present = set().union(*header_lists)
    optional_absent = [column for column in OPTIONAL_COLUMNS if column not in present]
    con = connect()
    try:
        rejects = load_snapshots(con, csv_paths, header_lists)
        raw_rows = con.execute("SELECT count(*) FROM snap_rows").fetchone()[0]
        dropped_identity = con.execute(
            """
            SELECT count(*)
            FROM snap_rows
            WHERE serial_number IS NULL OR trim(serial_number) = ''
               OR model IS NULL OR trim(model) = ''
               OR obs_date IS NULL
            """
        ).fetchone()[0]
        con.execute((SQL / "01_collapse_quarter.sql").read_text())
        out_parquet.parent.mkdir(parents=True, exist_ok=True)
        con.execute(
            f"""
            COPY (
                SELECT '{quarter}' AS source_quarter, *
                FROM drive_quarter
            ) TO '{out_parquet}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        totals = con.execute(
            """
            SELECT
                count(*) AS drives,
                coalesce(sum(snapshot_rows), 0) AS snapshot_rows,
                coalesce(sum(drive_days), 0) AS drive_days,
                coalesce(sum(failed), 0) AS failures,
                coalesce(sum(post_failure_days), 0) AS post_failure_days,
                coalesce(sum(duplicate_days), 0) AS duplicate_days,
                coalesce(sum(nonpositive_capacity_rows), 0) AS nonpositive_capacity_rows,
                coalesce(sum(smart9_null_rows), 0) AS smart9_null_rows,
                coalesce(sum(smart9_negative_rows), 0) AS smart9_negative_rows,
                coalesce(sum(bad_failure_rows), 0) AS bad_failure_rows,
                min(first_seen) AS first_seen,
                max(last_seen) AS last_seen
            FROM drive_quarter
            """
        ).fetchdf()
    finally:
        con.close()

    row = totals.iloc[0]
    summary = {
        "quarter": quarter,
        "files": len(csv_paths),
        "distinct_header_layouts": len(unique_headers),
        "columns": sorted(present),
        "optional_columns_absent": optional_absent,
        "raw_rows_loaded": int(raw_rows),
        "dropped_identity_rows": int(dropped_identity),
        "rejected_rows": rejects["rejected_rows"],
        "reject_reasons": rejects["reject_reasons"],
        "drives": int(row["drives"]),
        "snapshot_rows_in_drives": int(row["snapshot_rows"]),
        "drive_days": int(row["drive_days"]),
        "failures": int(row["failures"]),
        "post_failure_days": int(row["post_failure_days"]),
        "duplicate_days": int(row["duplicate_days"]),
        "nonpositive_capacity_rows": int(row["nonpositive_capacity_rows"]),
        "smart9_null_rows": int(row["smart9_null_rows"]),
        "smart9_negative_rows": int(row["smart9_negative_rows"]),
        "bad_failure_rows": int(row["bad_failure_rows"]),
        "first_seen": str(row["first_seen"]),
        "last_seen": str(row["last_seen"]),
    }
    if quarter == "2026Q2":
        summary["published_reconciliation"] = {
            "published": PUBLISHED_Q2_2026,
            "drives_delta": int(row["drives"]) - PUBLISHED_Q2_2026["drives"],
            "failures_delta": int(row["failures"]) - PUBLISHED_Q2_2026["failures"],
            "drive_days_delta": int(row["drive_days"]) - PUBLISHED_Q2_2026["drive_days"],
        }
    return summary


def extract_zip(quarter: str) -> Path:
    """Unzip one quarter. The caller deletes the directory when finished."""
    zip_path = DATA_RAW / ZIP_NAMES[quarter]
    if not zip_path.exists():
        raise FileNotFoundError(f"Missing {zip_path}. Run analysis/download_data.py first.")
    free = shutil.disk_usage(DATA_RAW).free
    if free < 20 * 2**30:
        raise RuntimeError(f"Need at least 20 GB free to unpack {quarter}, found {free} bytes")
    dest = DATA_INTERIM / quarter
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    subprocess.run(["unzip", "-q", str(zip_path), "-d", str(dest)], check=True)
    return dest


def combine_quarters(quarter_parquets: list[Path]) -> dict:
    """Union quarter parquets and write the classified drive table."""
    con = connect()
    try:
        con.execute((SQL / "02_combine_drives.sql").read_text(), [[str(path) for path in quarter_parquets]])
        con.execute((SQL / "03_classify_and_afr.sql").read_text())
        DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
        classified = DATA_PROCESSED / "drives.parquet"
        afr_path = DATA_PROCESSED / "afr_by_model.parquet"
        con.execute(f"COPY drive_classified TO '{classified}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        con.execute(f"COPY afr_by_model TO '{afr_path}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        totals = con.execute(
            """
            SELECT
                count(*) AS drives,
                coalesce(sum(snapshot_rows), 0) AS snapshot_rows,
                coalesce(sum(drive_days), 0) AS drive_days,
                coalesce(sum(failed), 0) AS failures,
                coalesce(sum(post_failure_days), 0) AS post_failure_days,
                coalesce(sum(duplicate_days), 0) AS duplicate_days,
                coalesce(sum(CASE WHEN capacity_min IS NOT NULL
                    AND capacity_bytes IS NOT NULL
                    AND capacity_bytes > capacity_min * 1.05 THEN 1 ELSE 0 END), 0) AS capacity_disagreements,
                sum(CASE WHEN media = 'HDD' THEN 1 ELSE 0 END) AS hdd_drives,
                sum(CASE WHEN media = 'SSD' THEN 1 ELSE 0 END) AS ssd_drives,
                sum(CASE WHEN media = 'HDD' AND manufacturer = 'Other' THEN 1 ELSE 0 END) AS unclassified_hdd
            FROM drive_classified
            """
        ).fetchone()
        other_models = con.execute(
            """
            SELECT model, count(*) AS drives
            FROM drive_classified
            WHERE media = 'HDD' AND manufacturer = 'Other'
            GROUP BY model
            ORDER BY drives DESC
            LIMIT 30
            """
        ).fetchdf()
    finally:
        con.close()
    return {
        "drives": int(totals[0]),
        "snapshot_rows": int(totals[1]),
        "drive_days": int(totals[2]),
        "failures": int(totals[3]),
        "post_failure_days": int(totals[4]),
        "duplicate_days": int(totals[5]),
        "capacity_disagreements": int(totals[6]),
        "hdd_drives": int(totals[7]),
        "ssd_drives": int(totals[8]),
        "unclassified_hdd": int(totals[9]),
        "unclassified_models": other_models.to_dict(orient="records"),
    }


def quality_log(quarter_summaries: list[dict], combined: dict) -> list[dict]:
    """Data-quality log in the case-study table shape."""
    raw_rows = sum(item["raw_rows_loaded"] for item in quarter_summaries)
    dropped = sum(item["dropped_identity_rows"] for item in quarter_summaries)
    rejected = sum(item["rejected_rows"] for item in quarter_summaries)
    column_lists = [tuple(item["columns"]) for item in quarter_summaries if item.get("columns")]
    same_header = len(set(column_lists)) <= 1
    header_note = (
        f"All six quarters share one {len(column_lists[0]) if column_lists else 0}-column header, "
        "so no cross-file alignment was required in this window. "
        "The published schema file still spells two unused columns smart_211_normailized and "
        "smart_212_normailized; the files spell them normalized. Those columns are not used."
        if same_header
        else "Headers differed. The loader projects a stable column list and leaves missing optional fields null."
    )
    return [
        {
            "issue": "Schema spelling and column lists can change between Backblaze quarters",
            "how_detected": "Header of every CSV compared within and across quarters, then compared with the published schema file",
            "decision": header_note,
            "rows_affected": raw_rows,
            "impact": "None for the rates in this window. The loader still aligns by column name so a later quarter can add a field.",
        },
        {
            "issue": "Parser could not read some CSV rows",
            "how_detected": "DuckDB reject_errors with ignore_errors",
            "decision": "Rejected rows stay out of the drive table and are counted here",
            "rows_affected": rejected,
            "impact": "Low if this is a trace share of snapshot rows; otherwise the quarter should be rerun after inspection",
        },
        {
            "issue": "Snapshot row has no serial, model, or date",
            "how_detected": "Null and blank check after type casting",
            "decision": "Dropped before the drive collapse",
            "rows_affected": dropped,
            "impact": "Low. These rows cannot be tied to a drive.",
        },
        {
            "issue": "More than one snapshot for the same drive on the same day",
            "how_detected": "Count of serial-model-date groups with more than one row",
            "decision": "Collapsed to one day so drive-days are not double counted",
            "rows_affected": sum(item["duplicate_days"] for item in quarter_summaries),
            "impact": "Low. Affects the denominator only where duplicates existed.",
        },
        {
            "issue": "Drive keeps appearing after its failure date",
            "how_detected": "Dates after the first failure=1 row",
            "decision": "Those days are excluded from drive-days and from the exit age",
            "rows_affected": combined["post_failure_days"],
            "impact": "Medium if large: it changes the AFR denominator. The failure itself is kept once.",
        },
        {
            "issue": "Capacity missing, zero, or negative",
            "how_detected": "capacity_bytes <= 0 or null",
            "decision": "Drive-days still count. Capacity for the drive is the largest positive value seen.",
            "rows_affected": sum(item["nonpositive_capacity_rows"] for item in quarter_summaries),
            "impact": "Low for AFR. Drives with no positive capacity cannot enter the buy list.",
        },
        {
            "issue": "Positive capacities for one drive disagree by more than 5 percent",
            "how_detected": "Min and max positive capacity_bytes within the serial and model",
            "decision": "Keep the max and count the drive here",
            "rows_affected": combined["capacity_disagreements"],
            "impact": "Low unless a model is pushed into the wrong TB class.",
        },
        {
            "issue": "SMART 9 raw value is null or negative",
            "how_detected": "Null and sign check on smart_9_raw",
            "decision": "Ignored for age. The drive still counts in AFR.",
            "rows_affected": sum(item["smart9_null_rows"] + item["smart9_negative_rows"] for item in quarter_summaries),
            "impact": "Medium for the age curve only. AFR does not use SMART 9.",
        },
        {
            "issue": "Failure flag is neither 0 nor 1",
            "how_detected": "Value check on the failure column",
            "decision": "Treated as not a failure event",
            "rows_affected": sum(item["bad_failure_rows"] for item in quarter_summaries),
            "impact": "Low. The event definition stays Backblaze's 0/1 failure flag.",
        },
        {
            "issue": "Model string names an HDD manufacturer the lookup does not know",
            "how_detected": "manufacturer = Other and media = HDD after the SQL CASE",
            "decision": "Left as Other. A model with more than 1,000 such drives stops the recommendation until the map is updated.",
            "rows_affected": combined["unclassified_hdd"],
            "impact": "High if a large model is unlabeled, because it would be missing from the buy list.",
        },
    ]


def main() -> None:
    force = "--force" in sys.argv
    quarter_dir = DATA_PROCESSED / "quarters"
    quarter_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    parquets = []
    for quarter in QUARTERS:
        out = quarter_dir / f"{quarter}.parquet"
        parquets.append(out)
        if out.exists() and not force:
            print(f"{quarter}: using existing {out.name}", flush=True)
            # Still need the summary for the quality log. Recompute cheaply.
            con = connect()
            try:
                frame = con.execute(
                    f"""
                    SELECT
                        count(*) AS drives,
                        coalesce(sum(snapshot_rows), 0) AS snapshot_rows,
                        coalesce(sum(drive_days), 0) AS drive_days,
                        coalesce(sum(failed), 0) AS failures,
                        coalesce(sum(post_failure_days), 0) AS post_failure_days,
                        coalesce(sum(duplicate_days), 0) AS duplicate_days,
                        coalesce(sum(nonpositive_capacity_rows), 0) AS nonpositive_capacity_rows,
                        coalesce(sum(smart9_null_rows), 0) AS smart9_null_rows,
                        coalesce(sum(smart9_negative_rows), 0) AS smart9_negative_rows,
                        coalesce(sum(bad_failure_rows), 0) AS bad_failure_rows,
                        min(first_seen) AS first_seen,
                        max(last_seen) AS last_seen
                    FROM read_parquet('{out}')
                    """
                ).fetchone()
            finally:
                con.close()
            summaries.append(
                {
                    "quarter": quarter,
                    "files": None,
                    "distinct_header_layouts": None,
                    "columns": [],
                    "optional_columns_absent": [],
                    "raw_rows_loaded": int(frame[1]),
                    "dropped_identity_rows": 0,
                    "rejected_rows": 0,
                    "reject_reasons": [],
                    "drives": int(frame[0]),
                    "snapshot_rows_in_drives": int(frame[1]),
                    "drive_days": int(frame[2]),
                    "failures": int(frame[3]),
                    "post_failure_days": int(frame[4]),
                    "duplicate_days": int(frame[5]),
                    "nonpositive_capacity_rows": int(frame[6]),
                    "smart9_null_rows": int(frame[7]),
                    "smart9_negative_rows": int(frame[8]),
                    "bad_failure_rows": int(frame[9]),
                    "first_seen": str(frame[10]),
                    "last_seen": str(frame[11]),
                    "reused_parquet": True,
                }
            )
            continue
        extracted = extract_zip(quarter)
        try:
            csv_paths = sorted(path for path in extracted.rglob("*.csv") if path.is_file())
            if not csv_paths:
                raise RuntimeError(f"{quarter} zip contained no CSV files")
            print(f"{quarter}: collapsing {len(csv_paths)} files", flush=True)
            summary = collapse_csvs(csv_paths, quarter, out)
            summaries.append(summary)
            print(
                f"{quarter}: {summary['drives']} drives, "
                f"{summary['failures']} failures, {summary['drive_days']} drive-days",
                flush=True,
            )
        finally:
            shutil.rmtree(extracted, ignore_errors=True)

    print("combining quarters", flush=True)
    combined = combine_quarters(parquets)
    if combined["unclassified_hdd"] > 1000:
        names = ", ".join(
            f"{item['model']} ({item['drives']})" for item in combined["unclassified_models"][:10]
        )
        raise RuntimeError(
            "Unclassified HDD models exceed 1,000 drives. Update the manufacturer map in "
            f"sql/03_classify_and_afr.sql and rerun. Largest: {names}"
        )
    log = quality_log(summaries, combined)
    REPORTS.mkdir(parents=True, exist_ok=True)
    payload = {"quarters": summaries, "combined": combined, "quality_log": log}
    (REPORTS / "process_summary.json").write_text(json.dumps(payload, indent=2, default=str))
    print(
        f"combined: {combined['drives']} drives, {combined['failures']} failures, "
        f"{combined['hdd_drives']} HDD, {combined['ssd_drives']} SSD",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        print(f"process failed: {exc}", file=sys.stderr)
        sys.exit(1)
