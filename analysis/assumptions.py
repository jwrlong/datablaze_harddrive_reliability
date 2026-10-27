"""Planning assumptions and decision rules.

These values are inputs, not estimates. They are fixed before model
annualized failure rates are inspected. Changing one of them changes the
recommendation, so the memo names each one.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
DATA_INTERIM = ROOT / "data" / "interim"
DATA_PROCESSED = ROOT / "data" / "processed"
DATA_TMP = ROOT / "data" / "tmp"
REPORTS = ROOT / "reports"
FIGURES = ROOT / "docs" / "figures"
DASHBOARD = ROOT / "dashboard"
SQL = ROOT / "sql"

# Public zip files. Raw drive rows are downloaded here and are not redistributed.
SOURCE_PAGE = "https://www.backblaze.com/cloud-storage/resources/hard-drive-test-data"
ZIP_BASE = "https://f001.backblazeb2.com/file/Backblaze-Hard-Drive-Data"
QUARTERS = ("2025Q1", "2025Q2", "2025Q3", "2025Q4", "2026Q1", "2026Q2")
ZIP_NAMES = {
    "2025Q1": "data_Q1_2025.zip",
    "2025Q2": "data_Q2_2025.zip",
    "2025Q3": "data_Q3_2025.zip",
    "2025Q4": "data_Q4_2025.zip",
    "2026Q1": "data_Q1_2026.zip",
    "2026Q2": "data_Q2_2026.zip",
}
LATEST_QUARTER = QUARTERS[-1]

# Backblaze's published Q2 2026 snapshot, read from the source page on
# 2026-10-23. Used only as a pipeline reconciliation check.
PUBLISHED_Q2_2026 = {
    "drives": 354_415,
    "failures": 1_498,
    "drive_days": 31_553_350,
    "afr": 0.0173,
}

# Annualization uses a 365-day year, matching Backblaze's published AFR.
DAYS_PER_YEAR = 365
HOURS_PER_YEAR = 24 * DAYS_PER_YEAR

# A model can be recommended or stopped only with this much evidence.
MIN_DRIVES = 200
MIN_DRIVE_DAYS = 100_000

# Stability checks on the cost screen. They were added after that screen
# crowned a model with 23 failures that had never been fielded in two large
# datacenters. A rerun applies the same checks.
MIN_FAILURES_FOR_STANDARD = 50
MIN_FAILURES_WELL_MEASURED = 100
LARGE_DATACENTER_SHARE = 0.10

# New capacity for this buy is limited to the current generation.
MIN_CAPACITY_TB = 8

# Point AFR may sit at most 25% above the fleet AFR and still be buyable.
# A model whose lower 95% bound is above the fleet AFR is a stop, regardless.
AFR_TOLERANCE = 1.25

# Age scale. SMART 9 is power-on time. The median raw value per model
# chooses the unit. Values in the gap between these cutoffs are not converted.
SMART9_HOURS_MAX_MEDIAN = 150_000  # 17.1 years; a fleet median above this is not hours
SMART9_MINUTES_MIN_MEDIAN = 500_000
SMART9_SECONDS_MIN_MEDIAN = 10_000_000
MAX_AGE_YEARS = 15.0

# Life table. Baseline is the wear-in plateau, after early life and before
# the ages where wear-out is usually discussed. A sharp rise is the first
# later band that clears the evidence gates and doubles that baseline.
BAND_YEARS = 0.5
BASELINE_AGE_LO = 1.0
BASELINE_AGE_HI = 3.0
SHARP_MIN_AGE = 3.0
SHARP_RATIO = 2.0
BAND_MIN_DRIVE_YEARS = 200.0
BAND_MIN_FAILURES = 5
LIFE_TABLE_MAX_AGE = 10.0

# Spares. The mission, prices, lead time, service level, and budget are
# planning assumptions for a fictional site, not Backblaze's budget.
MISSION_TB = 100_000  # 100 PB raw
LEAD_TIME_DAYS = 42
SERVICE_LEVEL = 0.99
SPARE_BUDGET_USD = 100_000
DUAL_SOURCE_SHARE = 0.20
SENSITIVITY_LEAD_DAYS = (21, 42, 84)
SENSITIVITY_SERVICE = (0.95, 0.99)
SCALE_FLEET_SIZES = (500, 1_000, 2_000, 5_000, 10_000)

# Larger drives are given a lower planning price per TB so the optimizer
# has a real cost difference. These are not vendor quotes.
USD_PER_TB = {
    8: 22.0,
    10: 20.0,
    12: 18.0,
    14: 17.0,
    16: 16.0,
    18: 15.0,
    20: 15.0,
    22: 14.0,
    24: 14.0,
    26: 14.0,
}
USD_PER_TB_ABOVE = 14.0
USD_PER_TB_BELOW = 24.0


def usd_per_tb(capacity_tb: int) -> float:
    """Return the planning price per TB for a nominal capacity."""
    if capacity_tb in USD_PER_TB:
        return USD_PER_TB[capacity_tb]
    if capacity_tb > 26:
        return USD_PER_TB_ABOVE
    return USD_PER_TB_BELOW


def drive_price_usd(capacity_tb: int) -> float:
    """Return the planning price of one drive."""
    return usd_per_tb(capacity_tb) * capacity_tb
