"""Rates, life table, and the spare-stocking optimization.

The decision labels are functions of these rules only. Nothing in here
looks at a model name and special-cases it.
"""

from __future__ import annotations

from math import ceil, floor

import numpy as np
import pandas as pd
from scipy.stats import chi2, poisson

from analysis.assumptions import (
    AFR_TOLERANCE,
    BAND_MIN_DRIVE_YEARS,
    BAND_MIN_FAILURES,
    BAND_YEARS,
    BASELINE_AGE_HI,
    BASELINE_AGE_LO,
    DAYS_PER_YEAR,
    DUAL_SOURCE_SHARE,
    HOURS_PER_YEAR,
    LEAD_TIME_DAYS,
    LIFE_TABLE_MAX_AGE,
    MAX_AGE_YEARS,
    MIN_CAPACITY_TB,
    MIN_DRIVE_DAYS,
    MIN_DRIVES,
    MISSION_TB,
    SCALE_FLEET_SIZES,
    SERVICE_LEVEL,
    SHARP_MIN_AGE,
    SHARP_RATIO,
    SMART9_HOURS_MAX_MEDIAN,
    SMART9_MINUTES_MIN_MEDIAN,
    SMART9_SECONDS_MIN_MEDIAN,
    SPARE_BUDGET_USD,
    drive_price_usd,
)


def poisson_rate_ci(events: int, exposure: float, alpha: float = 0.05) -> tuple[float, float]:
    """Garwood confidence interval for a Poisson rate.

    events is the failure count and exposure is the time at risk in the
    same units the caller will annualize. Zero events produce a lower
    bound of zero.
    """
    if exposure <= 0:
        return (float("nan"), float("nan"))
    if events < 0:
        raise ValueError("events must be non-negative")
    if events == 0:
        lower = 0.0
    else:
        lower = 0.5 * chi2.ppf(alpha / 2.0, 2 * events) / exposure
    upper = 0.5 * chi2.ppf(1.0 - alpha / 2.0, 2 * (events + 1)) / exposure
    return float(lower), float(upper)


def annualized_failure_rate(failures: int, drive_days: float) -> float:
    """Backblaze AFR: failures per drive-day, scaled to a 365-day year."""
    if drive_days <= 0:
        return float("nan")
    return float(failures) * DAYS_PER_YEAR / float(drive_days)


def afr_interval(failures: int, drive_days: float, alpha: float = 0.05) -> tuple[float, float]:
    """Annualized Garwood interval. Exposure is drive-days."""
    lower, upper = poisson_rate_ci(failures, drive_days, alpha)
    return lower * DAYS_PER_YEAR, upper * DAYS_PER_YEAR


def smart9_scale(median_value: float, nonnull_share: float) -> tuple[str, float | None]:
    """Choose the SMART 9 divisor for one model from the median raw value.

    The median is used because a few corrupted counters should not flip the
    unit. A median that falls between the hour, minute, and second ranges
    is left unconverted.
    """
    if nonnull_share < 0.5 or median_value is None or not np.isfinite(median_value):
        return "missing", None
    if median_value <= 0:
        return "missing", None
    if median_value <= SMART9_HOURS_MAX_MEDIAN:
        return "hours", 1.0
    if median_value >= SMART9_SECONDS_MIN_MEDIAN:
        return "seconds", 3600.0
    if median_value >= SMART9_MINUTES_MIN_MEDIAN:
        return "minutes", 60.0
    return "ambiguous", None


def classify_model(
    *,
    media: str,
    in_latest_quarter: bool,
    drives: int,
    drive_days: float,
    capacity_tb: float | None,
    afr: float,
    ci_low: float,
    fleet_afr: float,
    min_drives: int = MIN_DRIVES,
    min_drive_days: int = MIN_DRIVE_DAYS,
    min_capacity_tb: int = MIN_CAPACITY_TB,
    afr_tolerance: float = AFR_TOLERANCE,
) -> str:
    """Return one decision label for a model.

    Stop requires the lower bound to clear the fleet rate, so a noisy
    high point estimate is not enough. Approved also caps the point
    estimate at AFR_TOLERANCE times the fleet rate.
    """
    if media != "HDD":
        return "not_hdd"
    if not in_latest_quarter:
        return "not_in_current_fleet"
    if drives < min_drives or drive_days < min_drive_days:
        return "insufficient_evidence"
    if capacity_tb is None or capacity_tb < min_capacity_tb:
        return "legacy_capacity"
    if ci_low > fleet_afr:
        return "stop"
    if afr <= fleet_afr * afr_tolerance:
        return "approved"
    return "watch"


def spares_required(mean_failures: float, service: float = SERVICE_LEVEL) -> int:
    """Smallest spare count whose Poisson CDF meets the service level.

    For a discrete distribution this is the quantile: the smallest integer
    s with P(K <= s) >= service, K ~ Poisson(mean_failures).
    """
    if mean_failures < 0:
        raise ValueError("mean_failures must be non-negative")
    if service <= 0 or service >= 1:
        raise ValueError("service must be between 0 and 1")
    if mean_failures == 0:
        return 0
    return int(poisson.ppf(service, mean_failures))


def expected_unmet(mean_failures: float, spares: int) -> float:
    """Expected failed drives that spares do not cover, E[(K - s)+]."""
    if spares < 0:
        raise ValueError("spares must be non-negative")
    if mean_failures < 0:
        raise ValueError("mean_failures must be non-negative")
    if mean_failures == 0:
        return 0.0
    if spares == 0:
        return float(mean_failures)
    survival_at_s = 1.0 - poisson.cdf(spares - 1, mean_failures)
    survival_after_s = 1.0 - poisson.cdf(spares, mean_failures)
    return float(mean_failures * survival_at_s - spares * survival_after_s)


def mean_failures(drives: int, afr: float, lead_days: int) -> float:
    """Expected failures over a lead time if each drive fails at AFR."""
    if drives < 0:
        raise ValueError("drives must be non-negative")
    if afr < 0:
        raise ValueError("afr must be non-negative")
    return drives * afr * (lead_days / DAYS_PER_YEAR)


def stocking_plan(
    drives: int,
    afr: float,
    capacity_tb: int,
    *,
    lead_days: int = LEAD_TIME_DAYS,
    service: float = SERVICE_LEVEL,
    spare_budget_usd: float = SPARE_BUDGET_USD,
) -> dict:
    """Spares for one deployed fleet, reduced if the spare budget binds.

    The service level is a constraint. When the unconstrained spare buy
    costs more than the budget, the plan buys the most spares the budget
    allows and reports the service level that quantity actually reaches.
    """
    price = drive_price_usd(int(capacity_tb))
    mu = mean_failures(drives, afr, lead_days)
    s_star = spares_required(mu, service) if mu > 0 else 0
    unconstrained_cost = s_star * price
    budget_binds = unconstrained_cost > spare_budget_usd
    if price <= 0:
        s_budget = s_star
    else:
        s_budget = int(floor(spare_budget_usd / price))
    spares = s_budget if budget_binds else s_star
    achieved = 1.0 if mu == 0 else float(poisson.cdf(spares, mu))
    unmet = expected_unmet(mu, spares)
    unmet_at_target = expected_unmet(mu, s_star)
    return {
        "deployed_drives": int(drives),
        "afr": float(afr),
        "capacity_tb": int(capacity_tb),
        "drive_price_usd": price,
        "lead_days": int(lead_days),
        "service_target": float(service),
        "mean_failures": mu,
        "spares_at_target": s_star,
        "spares": spares,
        "spare_cost_usd": spares * price,
        "unconstrained_spare_cost_usd": unconstrained_cost,
        "budget_binds": bool(budget_binds),
        "achieved_service": achieved,
        "expected_unmet_drives": unmet,
        "expected_unmet_tb": unmet * capacity_tb,
        "expected_unmet_tb_at_target": unmet_at_target * capacity_tb,
        "zero_spare_shortfall_tb": mu * capacity_tb,
        "deployed_cost_usd": drives * price,
        "capital_usd": (drives + spares) * price,
    }


def _candidate_key(plan: dict) -> tuple:
    """Prefer a plan that hits the service level, then the cheaper one.

    Expected uncovered TB at 99% is a small residual. Letting it outrank
    capital would pick a smaller drive over a large price difference.
    Plans that miss the service level are ordered by the service they do
    reach, and only then by uncovered TB.
    """
    meets = plan["achieved_service"] + 1e-12 >= plan["service_target"]
    if meets:
        return (0, plan["capital_usd"], plan["expected_unmet_tb"], plan["model"])
    return (1, -plan["achieved_service"], plan["expected_unmet_tb"], plan["model"])


def optimize_standard(
    approved: pd.DataFrame,
    *,
    mission_tb: float = MISSION_TB,
    lead_days: int = LEAD_TIME_DAYS,
    service: float = SERVICE_LEVEL,
    spare_budget_usd: float = SPARE_BUDGET_USD,
) -> dict:
    """Pick a primary model and, if one exists, a second manufacturer.

    approved must contain model, manufacturer, capacity_tb, and afr.
    The objective is lexicographic: hit the service level inside the spare
    budget, then minimize expected uncovered TB, then minimize capital.
    """
    if approved.empty:
        return {"primary": None, "alternate": None, "single": None, "dual": None, "recommendation": "none"}

    singles = []
    for row in approved.itertuples(index=False):
        capacity = int(row.capacity_tb)
        deployed = int(ceil(mission_tb / capacity))
        observed = float(row.afr)
        planning = getattr(row, "planning_afr", None)
        if planning is None or not np.isfinite(planning):
            planning = observed
        plan = stocking_plan(
            deployed,
            float(planning),
            capacity,
            lead_days=lead_days,
            service=service,
            spare_budget_usd=spare_budget_usd,
        )
        plan["model"] = row.model
        plan["manufacturer"] = row.manufacturer
        plan["mission_tb"] = mission_tb
        plan["observed_afr"] = observed
        plan["planning_afr"] = float(planning)
        singles.append(plan)
    singles.sort(key=_candidate_key)
    primary = singles[0]
    alternate = next(
        (item for item in singles if item["manufacturer"] != primary["manufacturer"]),
        None,
    )

    dual = None
    if alternate is not None:
        primary_tb = mission_tb * (1.0 - DUAL_SOURCE_SHARE)
        alternate_tb = mission_tb * DUAL_SOURCE_SHARE
        primary_n = int(ceil(primary_tb / primary["capacity_tb"]))
        alternate_n = int(ceil(alternate_tb / alternate["capacity_tb"]))
        # Each source has its own spare budget share equal to the full
        # budget only at the sum. The constraint is on the sum, so each
        # side is priced at the target service and the sum is checked.
        primary_part = stocking_plan(
            primary_n,
            primary["afr"],
            primary["capacity_tb"],
            lead_days=lead_days,
            service=service,
            spare_budget_usd=spare_budget_usd,
        )
        # Price the alternate against the residual budget so the sum cannot
        # silently exceed the constraint inside stocking_plan.
        residual = max(0.0, spare_budget_usd - primary_part["spare_cost_usd"])
        alternate_part = stocking_plan(
            alternate_n,
            alternate["afr"],
            alternate["capacity_tb"],
            lead_days=lead_days,
            service=service,
            spare_budget_usd=residual if residual > 0 else 0.0,
        )
        # When the residual is zero, stocking_plan treats the budget as
        # binding and stocks nothing. Recompute the unconstrained alternate
        # so the report can show what dual source would have required.
        alternate_unconstrained = stocking_plan(
            alternate_n,
            alternate["afr"],
            alternate["capacity_tb"],
            lead_days=lead_days,
            service=service,
            spare_budget_usd=1e18,
        )
        spare_cost = (
            primary_part["spares_at_target"] * primary["drive_price_usd"]
            + alternate_unconstrained["spares_at_target"] * alternate["drive_price_usd"]
        )
        fits = spare_cost <= spare_budget_usd
        dual = {
            "fits_budget": fits,
            "spare_cost_usd": spare_cost,
            "primary_drives": primary_n,
            "alternate_drives": alternate_n,
            "primary_spares": primary_part["spares_at_target"],
            "alternate_spares": alternate_unconstrained["spares_at_target"],
            "primary_model": primary["model"],
            "alternate_model": alternate["model"],
            "expected_unmet_tb": (
                primary_part["expected_unmet_tb_at_target"]
                + alternate_unconstrained["expected_unmet_tb_at_target"]
            ),
            "zero_spare_shortfall_tb": (
                primary_part["zero_spare_shortfall_tb"]
                + alternate_unconstrained["zero_spare_shortfall_tb"]
            ),
            "capital_usd": (
                primary_n * primary["drive_price_usd"]
                + alternate_n * alternate["drive_price_usd"]
                + spare_cost
            ),
        }

    use_dual = dual is not None and dual["fits_budget"]
    return {
        "primary": primary,
        "alternate": alternate,
        "single": primary,
        "dual": dual,
        "candidates": singles,
        "recommendation": "dual" if use_dual else "single",
    }


def scale_table(afr: float, capacity_tb: int, lead_days: int = LEAD_TIME_DAYS) -> list[dict]:
    """Spare counts at fixed fleet sizes. Each row is recomputed, not scaled."""
    rows = []
    for drives in SCALE_FLEET_SIZES:
        plan = stocking_plan(drives, afr, capacity_tb, lead_days=lead_days, spare_budget_usd=1e18)
        rows.append(
            {
                "deployed_drives": drives,
                "mean_failures": plan["mean_failures"],
                "spares": plan["spares_at_target"],
                "zero_spare_shortfall_tb": plan["zero_spare_shortfall_tb"],
            }
        )
    return rows


def life_table(entry: np.ndarray, exit_: np.ndarray, event: np.ndarray) -> pd.DataFrame:
    """Piecewise constant hazard on power-on age, with left truncation.

    A drive contributes only the overlap of (entry, exit] with each band.
    A failure is counted in the band that contains the exit age.
    """
    entry = np.asarray(entry, dtype=float)
    exit_ = np.asarray(exit_, dtype=float)
    event = np.asarray(event, dtype=bool)
    edges = np.round(np.arange(0.0, LIFE_TABLE_MAX_AGE + BAND_YEARS, BAND_YEARS), 10)
    rows = []
    for index, (start, stop) in enumerate(zip(edges[:-1], edges[1:])):
        overlap = np.clip(np.minimum(exit_, stop) - np.maximum(entry, start), 0, None)
        exposure = float(overlap.sum())
        if index == len(edges) - 2:
            failed = int(np.sum(event & (exit_ >= start) & (exit_ <= stop)))
        else:
            failed = int(np.sum(event & (exit_ >= start) & (exit_ < stop)))
        hazard = failed / exposure if exposure > 0 else float("nan")
        low, high = poisson_rate_ci(failed, exposure) if exposure > 0 else (float("nan"), float("nan"))
        rows.append(
            {
                "age_start": float(start),
                "age_end": float(stop),
                "exposure_drive_years": exposure,
                "failures": failed,
                "hazard_per_year": hazard,
                "hazard_low": low,
                "hazard_high": high,
            }
        )
    return pd.DataFrame(rows)


def baseline_hazard(table: pd.DataFrame) -> dict:
    """Pooled hazard on the age 1 to 3 plateau."""
    mask = (table["age_start"] >= BASELINE_AGE_LO) & (table["age_end"] <= BASELINE_AGE_HI)
    window = table.loc[mask]
    exposure = float(window["exposure_drive_years"].sum())
    failures = int(window["failures"].sum())
    hazard = failures / exposure if exposure > 0 else float("nan")
    low, high = poisson_rate_ci(failures, exposure) if exposure > 0 else (float("nan"), float("nan"))
    return {
        "age_lo": BASELINE_AGE_LO,
        "age_hi": BASELINE_AGE_HI,
        "exposure_drive_years": exposure,
        "failures": failures,
        "hazard_per_year": hazard,
        "hazard_low": low,
        "hazard_high": high,
    }


def sharp_rise(table: pd.DataFrame, baseline: dict) -> dict:
    """First age at or after 3 years where hazard doubles the baseline.

    The band must have enough exposure and enough failures, and its lower
    confidence bound must sit above the baseline hazard. If no band qualifies,
    the result says so and still reports the age with the highest hazard.
    """
    base = baseline["hazard_per_year"]
    qualified = table[
        (table["age_start"] >= SHARP_MIN_AGE)
        & (table["exposure_drive_years"] >= BAND_MIN_DRIVE_YEARS)
        & (table["failures"] >= BAND_MIN_FAILURES)
    ].copy()
    found = None
    if np.isfinite(base) and base > 0 and baseline["exposure_drive_years"] >= BAND_MIN_DRIVE_YEARS:
        hits = qualified[
            (qualified["hazard_per_year"] >= SHARP_RATIO * base)
            & (qualified["hazard_low"] > base)
        ]
        if not hits.empty:
            found = hits.iloc[0]
    peak = None
    if not qualified.empty:
        peak = qualified.sort_values(["hazard_per_year", "age_start"], ascending=[False, True]).iloc[0]
    return {
        "found": found is not None,
        "age_years": None if found is None else float(found["age_start"]),
        "hazard_per_year": None if found is None else float(found["hazard_per_year"]),
        "ratio_to_baseline": None if found is None else float(found["hazard_per_year"] / base),
        "peak_age_years": None if peak is None else float(peak["age_start"]),
        "peak_hazard_per_year": None if peak is None else float(peak["hazard_per_year"]),
        "peak_ratio_to_baseline": None
        if peak is None or not np.isfinite(base) or base <= 0
        else float(peak["hazard_per_year"] / base),
    }


def infant_band(table: pd.DataFrame, baseline: dict) -> dict:
    """Whether the first half-year is above the age 1–3 baseline."""
    band = table[(table["age_start"] == 0.0) & (table["age_end"] == BAND_YEARS)]
    if band.empty:
        return {"elevated": False}
    row = band.iloc[0]
    base = baseline["hazard_per_year"]
    elevated = bool(
        row["exposure_drive_years"] >= BAND_MIN_DRIVE_YEARS
        and row["failures"] >= BAND_MIN_FAILURES
        and np.isfinite(base)
        and row["hazard_low"] > base
    )
    return {
        "elevated": elevated,
        "hazard_per_year": float(row["hazard_per_year"]) if np.isfinite(row["hazard_per_year"]) else None,
        "hazard_low": float(row["hazard_low"]) if np.isfinite(row["hazard_low"]) else None,
        "hazard_high": float(row["hazard_high"]) if np.isfinite(row["hazard_high"]) else None,
        "failures": int(row["failures"]),
        "exposure_drive_years": float(row["exposure_drive_years"]),
    }


def age_fields(entry_hours: pd.Series, exit_hours: pd.Series, drive_days: pd.Series) -> pd.DataFrame:
    """Power-on age at entry and exit, and whether the row can enter survival.

    The counter has to move forward, stay inside a credible drive life, and
    not advance faster than the calendar days we actually observed.
    """
    entry_years = entry_hours / HOURS_PER_YEAR
    exit_years = exit_hours / HOURS_PER_YEAR
    delta = exit_years - entry_years
    calendar = drive_days / DAYS_PER_YEAR
    age_ok = (
        entry_hours.notna()
        & exit_hours.notna()
        & (entry_hours > 0)
        & (exit_years > entry_years)
        & (entry_years <= MAX_AGE_YEARS)
        & (exit_years <= MAX_AGE_YEARS)
        & (delta <= calendar + 0.5)
        & (drive_days >= 2)
    )
    return pd.DataFrame(
        {
            "entry_years": entry_years,
            "exit_years": exit_years,
            "age_ok": age_ok.fillna(False),
        }
    )
