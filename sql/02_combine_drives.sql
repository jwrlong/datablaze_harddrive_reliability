-- Stack quarter-level drive rows into one row per serial and model.
-- drive_days are summed. Quarters do not share calendar dates, so the sum
-- is the count of observed drive-days. A failure is kept once.

CREATE OR REPLACE TABLE drives AS
SELECT
    serial_number,
    model,
    count(DISTINCT source_quarter) AS quarters_seen,
    string_agg(DISTINCT source_quarter, ',' ORDER BY source_quarter) AS quarters,
    sum(snapshot_rows) AS snapshot_rows,
    sum(drive_days) AS drive_days,
    min(first_seen) AS first_seen,
    max(last_seen) AS last_seen,
    max(failed) AS failed,
    min(failure_date) FILTER (WHERE failed = 1) AS failure_date,
    max(capacity_bytes) AS capacity_bytes,
    min(capacity_min) AS capacity_min,
    sum(nonpositive_capacity_rows) AS nonpositive_capacity_rows,
    arg_min(smart9_entry, first_seen) FILTER (WHERE smart9_entry IS NOT NULL) AS smart9_entry,
    CASE
        WHEN max(failed) = 1 THEN arg_min(smart9_exit, failure_date) FILTER (
            WHERE failed = 1 AND smart9_exit IS NOT NULL
        )
        ELSE arg_max(smart9_exit, last_seen) FILTER (WHERE smart9_exit IS NOT NULL)
    END AS smart9_exit,
    sum(smart9_null_rows) AS smart9_null_rows,
    sum(smart9_negative_rows) AS smart9_negative_rows,
    sum(bad_failure_rows) AS bad_failure_rows,
    sum(post_failure_days) AS post_failure_days,
    sum(duplicate_days) AS duplicate_days,
    arg_max(datacenter, last_seen) AS datacenter,
    max(is_legacy_format) AS is_legacy_format
FROM read_parquet(?)
GROUP BY serial_number, model;
