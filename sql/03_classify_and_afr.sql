-- Manufacturer and media are assigned from the model string.
-- AFR = failures / drive_days * 365, the Backblaze annualized failure rate.
-- Confidence intervals are added in Python; this query is the rate itself.

CREATE OR REPLACE TABLE drive_classified AS
SELECT
    d.*,
    CASE
        WHEN model LIKE '%SSD%'
          OR model LIKE 'MTFD%'
          OR model LIKE 'SAMSUNG%'
          OR model LIKE 'INTEL%'
          OR model LIKE 'MICRON%'
          OR model LIKE 'KINGSTON%'
          OR model LIKE 'SANDISK%'
          OR model LIKE 'CT%'
          OR model LIKE 'DELL%'
            THEN 'SSD'
        ELSE 'HDD'
    END AS media,
    CASE
        WHEN model LIKE '%SSD%'
          OR model LIKE 'MTFD%'
          OR model LIKE 'SAMSUNG%'
          OR model LIKE 'INTEL%'
          OR model LIKE 'MICRON%'
          OR model LIKE 'KINGSTON%'
          OR model LIKE 'SANDISK%'
          OR model LIKE 'CT%'
          OR model LIKE 'DELL%'
            THEN 'Other'
        WHEN model LIKE 'ST%' OR model LIKE 'SEAGATE%' THEN 'Seagate'
        WHEN model LIKE 'HGST%'
          OR model LIKE 'HUH%'
          OR model LIKE 'HMS%'
          OR model LIKE 'HUS%'
          OR model LIKE 'HDN%'
          OR model LIKE 'HITACHI%'
            THEN 'HGST'
        WHEN model LIKE 'WUH%'
          OR model LIKE 'WUS%'
          OR model LIKE 'WDC%'
          OR model LIKE 'WD%'
            THEN 'WDC'
        WHEN model LIKE 'TOSHIBA%'
          OR model LIKE 'MG%'
          OR model LIKE 'HDWG%'
          OR model LIKE 'HDWE%'
          OR model LIKE 'DT%'
            THEN 'Toshiba'
        ELSE 'Other'
    END AS manufacturer,
    CASE
        WHEN capacity_bytes IS NULL OR capacity_bytes <= 0 THEN NULL
        ELSE CAST(round(capacity_bytes / 1000000000000.0, 0) AS INTEGER)
    END AS capacity_tb
FROM drives d;

CREATE OR REPLACE TABLE afr_by_model AS
SELECT
    model,
    any_value(manufacturer) AS manufacturer,
    any_value(media) AS media,
    any_value(capacity_tb) AS capacity_tb,
    count(*) AS drives,
    sum(drive_days) AS drive_days,
    sum(failed) AS failures,
    CASE
        WHEN sum(drive_days) = 0 THEN NULL
        ELSE sum(failed) * 365.0 / sum(drive_days)
    END AS afr
FROM drive_classified
WHERE drive_days > 0
GROUP BY model
ORDER BY drive_days DESC;
