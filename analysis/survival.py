"""Power-on age, Kaplan-Meier curves, and the Cox model.

Age is SMART 9 power-on time, not the calendar date the drive was first
seen. Drives enter the risk set at the age they already had on their first
snapshot (left truncation) and leave at failure or at the last snapshot
(right censoring).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter, KaplanMeierFitter
from lifelines.statistics import proportional_hazard_test

from analysis.assumptions import BAND_MIN_DRIVE_YEARS, BAND_MIN_FAILURES
from analysis.metrics import age_fields, smart9_scale


def apply_age(drives: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach power-on age and a per-model note of the SMART 9 unit."""
    unit_rows = []
    scales: dict[str, float | None] = {}
    for model, sub in drives.groupby("model", sort=False):
        values = sub["smart9_entry"]
        positive = values[values.notna() & (values > 0)]
        share = float(len(positive) / len(sub)) if len(sub) else 0.0
        median = float(positive.median()) if len(positive) else float("nan")
        unit, scale = smart9_scale(median, share)
        scales[str(model)] = scale
        unit_rows.append(
            {
                "model": str(model),
                "drives": int(len(sub)),
                "smart9_nonnull_share": share,
                "smart9_entry_median": None if not np.isfinite(median) else median,
                "unit": unit,
                "divisor": scale,
            }
        )
    out = drives.copy()
    out["smart9_divisor"] = out["model"].map(scales)
    out["entry_hours"] = out["smart9_entry"] / out["smart9_divisor"]
    out["exit_hours"] = out["smart9_exit"] / out["smart9_divisor"]
    ages = age_fields(out["entry_hours"], out["exit_hours"], out["drive_days"])
    out = pd.concat([out.reset_index(drop=True), ages.reset_index(drop=True)], axis=1)
    return out, pd.DataFrame(unit_rows)


def early_life(drives: pd.DataFrame) -> pd.DataFrame:
    """Failures and exposure while power-on age is under 1 year."""
    usable = drives[drives["age_ok"]].copy()
    rows = []
    if usable.empty:
        return pd.DataFrame(columns=["model", "early_exposure_years", "early_failures", "early_hazard"])
    for model, sub in usable.groupby("model", sort=False):
        entry = sub["entry_years"].to_numpy(dtype=float)
        exit_ = sub["exit_years"].to_numpy(dtype=float)
        event = sub["failed"].to_numpy(dtype=bool)
        exposure = float(np.clip(np.minimum(exit_, 1.0) - np.maximum(entry, 0.0), 0, None).sum())
        failures = int(np.sum(event & (exit_ >= 0.0) & (exit_ < 1.0)))
        hazard = failures / exposure if exposure > 0 else float("nan")
        rows.append(
            {
                "model": model,
                "early_exposure_years": exposure,
                "early_failures": failures,
                "early_hazard": hazard,
            }
        )
    return pd.DataFrame(rows)


def planning_rate(afr: float, early_hazard: float, early_exposure: float, early_failures: int) -> tuple[float, str]:
    """Use the first-year hazard only when it is estimated and higher.

    A higher first-year rate changes the spare buy. A lower one does not,
    because spares also have to cover drives that are already old.
    """
    if (
        early_exposure >= BAND_MIN_DRIVE_YEARS
        and early_failures >= BAND_MIN_FAILURES
        and early_hazard is not None
        and np.isfinite(early_hazard)
        and early_hazard > afr
    ):
        return float(early_hazard), "age 0-1 hazard"
    return float(afr), "all-age AFR"


def kaplan_meier(drives: pd.DataFrame, series: list[tuple[str, pd.DataFrame]]) -> list[dict]:
    """Left-truncated Kaplan-Meier curves. Each series is a label and a frame."""
    curves = []
    for label, sub in series:
        sub = sub[sub["age_ok"]].dropna(subset=["entry_years", "exit_years"])
        sub = sub[sub["exit_years"] > sub["entry_years"]]
        if len(sub) < 30:
            continue
        fitter = KaplanMeierFitter()
        fitter.fit(
            sub["exit_years"],
            event_observed=sub["failed"].astype(bool),
            entry=sub["entry_years"],
            label=label,
        )
        survival = fitter.survival_function_
        interval = fitter.confidence_interval_survival_function_
        times = survival.index.to_numpy(dtype=float)
        if len(times) > 800:
            chosen = np.linspace(0, len(times) - 1, 800).astype(int)
            times = times[chosen]
            survival = survival.iloc[chosen]
            interval = interval.iloc[chosen]
        curves.append(
            {
                "label": label,
                "drives": int(len(sub)),
                "failures": int(sub["failed"].sum()),
                "age_years": [float(value) for value in times],
                "survival": [float(value) for value in survival.iloc[:, 0].to_numpy()],
                "low": [float(value) for value in interval.iloc[:, 0].to_numpy()],
                "high": [float(value) for value in interval.iloc[:, 1].to_numpy()],
            }
        )
    return curves


def _cox_table(fitter: CoxPHFitter) -> list[dict]:
    summary = fitter.summary.reset_index().rename(columns={"index": "term", "covariate": "term"})
    if "term" not in summary.columns:
        summary = summary.rename(columns={summary.columns[0]: "term"})
    rows = []
    for _, row in summary.iterrows():
        rows.append(
            {
                "term": str(row["term"]),
                "hazard_ratio": float(row["exp(coef)"]),
                "hr_low": float(row["exp(coef) lower 95%"]),
                "hr_high": float(row["exp(coef) upper 95%"]),
                "p": float(row["p"]),
            }
        )
    return rows


def _ph_rows(result) -> list[dict]:
    summary = result.summary.reset_index()
    name_col = summary.columns[0]
    rows = []
    for _, row in summary.iterrows():
        rows.append({"term": str(row[name_col]), "p": float(row["p"]), "test": str(row.get("test_name", "rank"))})
    return rows


def fit_cox(drives: pd.DataFrame, *, formula_column: str, reference: str | None = None) -> dict:
    """Cox model on power-on age, with left truncation.

    formula_column is 'mfr' or 'model'. The reference level is the group
    with the most drives, unless the caller passes one. A small penalizer
    is used only if the unpenalized fit does not converge.
    """
    columns = ["exit_years", "entry_years", "failed", formula_column, "capacity_tb"]
    work = drives.dropna(subset=columns).copy()
    work = work[(work["exit_years"] > work["entry_years"]) & (work["capacity_tb"].notna())]
    work["failed"] = work["failed"].astype(int)
    if work["failed"].sum() < 20 or work[formula_column].nunique() < 2:
        return {"fit": False, "reason": "too few events or groups"}
    if reference is None or reference not in set(work[formula_column]):
        reference = work.groupby(formula_column).size().idxmax()
    safe_reference = str(reference).replace("'", "")
    formula = f"C({formula_column}, Treatment(reference='{safe_reference}'))"
    if formula_column == "mfr":
        formula = formula + " + capacity_tb"
    used = work[["exit_years", "entry_years", "failed", formula_column] + (["capacity_tb"] if formula_column == "mfr" else [])]
    fitter = CoxPHFitter()
    penalizer = 0.0
    try:
        fitter.fit(
            used,
            duration_col="exit_years",
            event_col="failed",
            entry_col="entry_years",
            formula=formula,
            show_progress=False,
        )
    except Exception:
        penalizer = 0.1
        fitter = CoxPHFitter(penalizer=penalizer)
        try:
            fitter.fit(
                used,
                duration_col="exit_years",
                event_col="failed",
                entry_col="entry_years",
                formula=formula,
                show_progress=False,
            )
        except Exception as exc:  # noqa: BLE001 — the memo has to say the fit failed
            return {"fit": False, "reason": str(exc), "reference": str(reference)}
    try:
        ph = proportional_hazard_test(fitter, used, time_transform="rank")
        ph_rows = _ph_rows(ph)
    except Exception as exc:  # noqa: BLE001
        ph_rows = []
        ph_error = str(exc)
    else:
        ph_error = None
    if ph_error or not ph_rows:
        status = "not_run"
    elif all(row["p"] >= 0.05 for row in ph_rows):
        status = "holds"
    else:
        status = "violated"
    return {
        "fit": True,
        "reference": str(reference),
        "formula": formula,
        "penalizer": penalizer,
        "drives": int(len(used)),
        "failures": int(used["failed"].sum()),
        "coefficients": _cox_table(fitter),
        "proportional_hazards": ph_rows,
        "proportional_hazards_hold": status == "holds",
        "proportional_hazards_status": status,
        "proportional_hazards_error": ph_error,
    }


def fit_calendar_cox(drives: pd.DataFrame) -> dict:
    """Cox model on time since the drive entered the window, with no left truncation.

    Lifelines cannot compute Schoenfeld residuals when entry times are used,
    so the age-scale model cannot take the proportional-hazards test. This
    model uses calendar follow-up as the clock and puts power-on age at
    entry in as a covariate, which the test can handle.
    """
    work = prepare_cox_frame(drives)
    work = work.dropna(subset=["entry_years", "drive_days", "capacity_tb", "mfr"]).copy()
    work["study_years"] = work["drive_days"] / 365.0
    work = work[(work["study_years"] > 0) & (work["entry_years"] >= 0)]
    work["failed"] = work["failed"].astype(int)
    if work["failed"].sum() < 20 or work["mfr"].nunique() < 2:
        return {"fit": False, "reason": "too few events or groups"}
    reference = str(work.groupby("mfr").size().idxmax())
    formula = f"C(mfr, Treatment(reference='{reference}')) + capacity_tb + entry_years"
    used = work[["study_years", "failed", "mfr", "capacity_tb", "entry_years"]]
    fitter = CoxPHFitter()
    penalizer = 0.0
    try:
        fitter.fit(used, duration_col="study_years", event_col="failed", formula=formula, show_progress=False)
    except Exception:
        penalizer = 0.1
        fitter = CoxPHFitter(penalizer=penalizer)
        try:
            fitter.fit(used, duration_col="study_years", event_col="failed", formula=formula, show_progress=False)
        except Exception as exc:  # noqa: BLE001
            return {"fit": False, "reason": str(exc)}
    try:
        ph = proportional_hazard_test(fitter, used, time_transform="rank")
        ph_rows = _ph_rows(ph)
        ph_error = None
    except Exception as exc:  # noqa: BLE001
        ph_rows = []
        ph_error = str(exc)
    if ph_error or not ph_rows:
        status = "not_run"
    elif all(row["p"] >= 0.05 for row in ph_rows):
        status = "holds"
    else:
        status = "violated"
    return {
        "fit": True,
        "reference": reference,
        "formula": formula,
        "penalizer": penalizer,
        "drives": int(len(used)),
        "failures": int(used["failed"].sum()),
        "coefficients": _cox_table(fitter),
        "proportional_hazards": ph_rows,
        "proportional_hazards_hold": status == "holds",
        "proportional_hazards_status": status,
        "proportional_hazards_error": ph_error,
        "time_scale": "years from first snapshot in the window",
    }


def prepare_cox_frame(drives: pd.DataFrame) -> pd.DataFrame:
    """HDD rows with a manufacturer level that has at least five failures."""
    work = drives[(drives["media"] == "HDD") & drives["age_ok"]].copy()
    failure_counts = work.groupby("manufacturer")["failed"].sum()
    keep = set(failure_counts[failure_counts >= 5].index)
    work["mfr"] = np.where(work["manufacturer"].isin(keep), work["manufacturer"], "Other")
    return work
