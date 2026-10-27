-- Collapse one quarter of daily snapshots to one row per drive.
-- The runner first materializes a temp table named snap_rows with a stable
-- column set, so files that gained or lost columns still land here.
-- Grain after this query: one row per serial_number and model.

CREATE OR REPLACE TEMP TABLE drive_quarter AS
WITH daily AS (
    SELECT
        upper(trim(serial_number)) AS serial_number,
        upper(trim(regexp_replace(model, '\s+', ' ', 'g'))) AS model,
        obs_date,
        max(CASE WHEN failure IN (0, 1) THEN failure END) AS failure,
        max(capacity_bytes) FILTER (WHERE capacity_bytes > 0) AS capacity_bytes,
        min(capacity_bytes) FILTER (WHERE capacity_bytes > 0) AS capacity_min,
        max(smart_9_raw) FILTER (WHERE smart_9_raw >= 0) AS smart_9_raw,
        any_value(datacenter) AS datacenter,
        max(
            CASE
                WHEN lower(coalesce(is_legacy_raw, '')) IN ('1', 'true', 't') THEN 1
                WHEN lower(coalesce(is_legacy_raw, '')) IN ('0', 'false', 'f') THEN 0
                ELSE NULL
            END
        ) AS is_legacy_format,
        count(*) AS snapshot_rows,
        count(*) FILTER (
            WHERE failure IS NOT NULL AND failure NOT IN (0, 1)
        ) AS bad_failure_rows,
        count(*) FILTER (
            WHERE capacity_bytes IS NULL OR capacity_bytes <= 0
        ) AS nonpositive_capacity_rows,
        count(*) FILTER (WHERE smart_9_raw IS NULL) AS smart9_null_rows,
        count(*) FILTER (WHERE smart_9_raw < 0) AS smart9_negative_rows
    FROM snap_rows
    WHERE serial_number IS NOT NULL
      AND trim(serial_number) <> ''
      AND model IS NOT NULL
      AND trim(model) <> ''
      AND obs_date IS NOT NULL
    GROUP BY 1, 2, 3
),
flagged AS (
    SELECT
        *,
        min(CASE WHEN failure = 1 THEN obs_date END)
            OVER (PARTITION BY serial_number, model) AS failure_date
    FROM daily
)
SELECT
    serial_number,
    model,
    sum(snapshot_rows) AS snapshot_rows,
    count(*) FILTER (
        WHERE failure_date IS NULL OR obs_date <= failure_date
    ) AS drive_days,
    min(obs_date) AS first_seen,
    max(obs_date) FILTER (
        WHERE failure_date IS NULL OR obs_date <= failure_date
    ) AS last_seen,
    max(CASE WHEN failure = 1 THEN 1 ELSE 0 END) AS failed,
    min(failure_date) AS failure_date,
    max(capacity_bytes) AS capacity_bytes,
    min(capacity_min) AS capacity_min,
    sum(nonpositive_capacity_rows) AS nonpositive_capacity_rows,
    arg_min(smart_9_raw, obs_date) FILTER (WHERE smart_9_raw IS NOT NULL) AS smart9_entry,
    arg_max(smart_9_raw, obs_date) FILTER (
        WHERE smart_9_raw IS NOT NULL
          AND (failure_date IS NULL OR obs_date <= failure_date)
    ) AS smart9_exit,
    sum(smart9_null_rows) AS smart9_null_rows,
    sum(smart9_negative_rows) AS smart9_negative_rows,
    sum(bad_failure_rows) AS bad_failure_rows,
    count(*) FILTER (
        WHERE failure_date IS NOT NULL AND obs_date > failure_date
    ) AS post_failure_days,
    count(*) FILTER (WHERE snapshot_rows > 1) AS duplicate_days,
    arg_max(datacenter, obs_date) AS datacenter,
    max(is_legacy_format) AS is_legacy_format
FROM flagged
GROUP BY serial_number, model;
