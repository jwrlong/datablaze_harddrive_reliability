"""Annualized failure rates, survival, and the spare-stocking decision.

Reads data/processed/drives.parquet. Writes reports/results.json and the
memo, dashboard, and figures. Decision thresholds live in assumptions.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from analysis.assumptions import (
    DATA_PROCESSED,
    DUAL_SOURCE_SHARE,
    LARGE_DATACENTER_SHARE,
    LATEST_QUARTER,
    MIN_DRIVE_DAYS,
    MIN_DRIVES,
    MIN_FAILURES_FOR_STANDARD,
    MIN_FAILURES_WELL_MEASURED,
    MISSION_TB,
    QUARTERS,
    REPORTS,
    SENSITIVITY_LEAD_DAYS,
    SENSITIVITY_SERVICE,
)
from analysis.figures import render_figures
from analysis.dashboard import render_dashboard
from analysis.memo import render_memo
from analysis.metrics import (
    afr_interval,
    annualized_failure_rate,
    baseline_hazard,
    classify_model,
    infant_band,
    life_table,
    optimize_standard,
    scale_table,
    sharp_rise,
    stocking_plan,
)
from analysis.survival import (
    apply_age,
    early_life,
    fit_calendar_cox,
    fit_cox,
    kaplan_meier,
    planning_rate,
    prepare_cox_frame,
)


def clean(value):
    """JSON-safe copies of numpy and pandas values."""
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, (np.floating,)):
        value = float(value)
    elif isinstance(value, (np.integer,)):
        return int(value)
    elif isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, pd.Timestamp):
        return str(value.date())
    return value


def _mode(values: pd.Series):
    modes = values.dropna().mode()
    if modes.empty:
        return None
    return int(modes.iloc[0])


def build_model_table(
    hdd: pd.DataFrame,
    present: set[str],
    *,
    alpha: float = 0.05,
    min_drives: int = MIN_DRIVES,
    min_drive_days: int = MIN_DRIVE_DAYS,
) -> tuple[pd.DataFrame, dict]:
    """One row per HDD model, with a confidence interval and a decision label."""
    fleet_failures = int(hdd["failed"].sum())
    fleet_days = float(hdd["drive_days"].sum())
    fleet_afr = annualized_failure_rate(fleet_failures, fleet_days)
    fleet_low, fleet_high = afr_interval(fleet_failures, fleet_days, alpha)
    table = (
        hdd.groupby("model", as_index=False)
        .agg(
            manufacturer=("manufacturer", "first"),
            media=("media", "first"),
            capacity_tb=("capacity_tb", _mode),
            drives=("serial_number", "size"),
            drive_days=("drive_days", "sum"),
            failures=("failed", "sum"),
        )
        .sort_values("drive_days", ascending=False)
    )
    lows = []
    highs = []
    afrs = []
    labels = []
    for row in table.itertuples(index=False):
        afr = annualized_failure_rate(int(row.failures), float(row.drive_days))
        low, high = afr_interval(int(row.failures), float(row.drive_days), alpha)
        afrs.append(afr)
        lows.append(low)
        highs.append(high)
        labels.append(
            classify_model(
                media=row.media,
                in_latest_quarter=row.model in present,
                drives=int(row.drives),
                drive_days=float(row.drive_days),
                capacity_tb=None if pd.isna(row.capacity_tb) else int(row.capacity_tb),
                afr=afr,
                ci_low=low,
                fleet_afr=fleet_afr,
                min_drives=min_drives,
                min_drive_days=min_drive_days,
            )
        )
    table["afr"] = afrs
    table["afr_low"] = lows
    table["afr_high"] = highs
    table["decision"] = labels
    table["in_latest_quarter"] = table["model"].isin(present)
    fleet = {
        "drives": int(len(hdd)),
        "drive_days": fleet_days,
        "failures": fleet_failures,
        "afr": fleet_afr,
        "afr_low": fleet_low,
        "afr_high": fleet_high,
    }
    return table, fleet


def attach_planning(table: pd.DataFrame, drives: pd.DataFrame) -> pd.DataFrame:
    """Add the rate the spare calculation will use for each model."""
    early = early_life(drives)
    merged = table.merge(early, on="model", how="left")
    rates = []
    bases = []
    for row in merged.itertuples(index=False):
        exposure = 0.0 if pd.isna(row.early_exposure_years) else float(row.early_exposure_years)
        failures = 0 if pd.isna(row.early_failures) else int(row.early_failures)
        hazard = float(row.early_hazard) if pd.notna(row.early_hazard) else float("nan")
        rate, basis = planning_rate(float(row.afr), hazard, exposure, failures)
        rates.append(rate)
        bases.append(basis)
    merged["planning_afr"] = rates
    merged["planning_basis"] = bases
    return merged


def group_rate(frame: pd.DataFrame, column: str) -> list[dict]:
    rows = []
    for key, sub in frame.groupby(column, dropna=False):
        failures = int(sub["failed"].sum())
        days = float(sub["drive_days"].sum())
        low, high = afr_interval(failures, days)
        rows.append(
            {
                "group": "unknown" if pd.isna(key) else str(key),
                "drives": int(len(sub)),
                "drive_days": days,
                "failures": failures,
                "afr": annualized_failure_rate(failures, days),
                "afr_low": low,
                "afr_high": high,
            }
        )
    rows.sort(key=lambda item: item["drive_days"], reverse=True)
    return rows


def model_alias_map(names: set[str]) -> dict[str, str]:
    """Map a bare model string onto the same model with a maker prefix.

    Backblaze writes both `WDC WUH721816ALE6L4` and `WUH721816ALE6L4`.
    Quarter files keep whichever spelling was on the snapshot, so a join
    after the combined table is canonicalized has to use the same map.
    """
    mapping = {}
    for name in names:
        for prefix in ("WDC ", "HGST ", "TOSHIBA ", "SEAGATE "):
            if name.startswith(prefix) and name[len(prefix):] in names:
                mapping[name[len(prefix):]] = name
    return mapping


def quarterly_rates(hdd: pd.DataFrame) -> list[dict]:
    frames = []
    quarter_dir = DATA_PROCESSED / "quarters"
    for quarter in QUARTERS:
        path = quarter_dir / f"{quarter}.parquet"
        frame = pd.read_parquet(path, columns=["source_quarter", "serial_number", "model", "drive_days", "failed"])
        frames.append(frame)
    stacked = pd.concat(frames, ignore_index=True)
    names = set(stacked["model"].dropna().unique()) | set(hdd["model"].dropna().unique())
    mapping = model_alias_map(names)
    if mapping:
        stacked["model"] = stacked["model"].replace(mapping)
    stacked = stacked.merge(hdd[["serial_number", "model"]], on=["serial_number", "model"], how="inner")
    rows = []
    for quarter, sub in stacked.groupby("source_quarter", sort=True):
        failures = int(sub["failed"].sum())
        days = float(sub["drive_days"].sum())
        low, high = afr_interval(failures, days)
        rows.append(
            {
                "quarter": str(quarter),
                "drives": int(sub["serial_number"].nunique()),
                "drive_days": days,
                "failures": failures,
                "afr": annualized_failure_rate(failures, days),
                "afr_low": low,
                "afr_high": high,
            }
        )
    return rows


def datacenter_rates(hdd: pd.DataFrame, primary: str | None) -> dict:
    known = hdd[hdd["datacenter"].notna() & (hdd["datacenter"].astype(str).str.strip() != "")]
    share = float(len(known) / len(hdd)) if len(hdd) else 0.0
    if share < 0.5:
        return {
            "usable": False,
            "share_of_drives": share,
            "note": "Datacenter was missing on most drives, so location was not used as a control.",
            "rows": [],
        }
    rows = [row for row in group_rate(known, "datacenter") if row["drive_days"] >= MIN_DRIVE_DAYS]
    afrs = [row["afr"] for row in rows if row["afr"] and row["afr"] > 0]
    ratio = max(afrs) / min(afrs) if len(afrs) >= 2 else None
    primary_rows = []
    if primary is not None:
        primary_rows = group_rate(known[known["model"] == primary], "datacenter")
        primary_rows = [row for row in primary_rows if row["drive_days"] >= 50_000]
    return {
        "usable": True,
        "share_of_drives": share,
        "max_min_ratio": ratio,
        "rows": rows,
        "primary_model": primary,
        "primary_rows": primary_rows,
        "note": (
            "Datacenter annualized rates are close enough that location does not dominate the fleet rate."
            if ratio is not None and ratio < 1.5
            else "Datacenter annualized rates differ by enough that model and location are mixed together."
        ),
    }


def canonicalize_models(drives: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Merge a model that is the same string with and without a maker prefix.

    Backblaze sometimes writes WDC WUH721816ALE6L4 and sometimes drops the
    WDC prefix. Treating those as two models makes the small slice look like
    its own failure rate.
    """
    mapping = model_alias_map(set(drives["model"].unique()))
    if not mapping:
        return drives, []
    out = drives.copy()
    out["model"] = out["model"].replace(mapping)
    notes = [
        {"from": source, "to": target, "drives": int((drives["model"] == source).sum())}
        for source, target in mapping.items()
    ]
    return out, notes


def covers_large_sites(hdd: pd.DataFrame, model: str, large_sites: set[str]) -> bool:
    present = set(hdd.loc[hdd["model"] == model, "datacenter"].dropna().unique())
    return large_sites <= present


def operational_buy(plan: dict, table: pd.DataFrame, hdd: pd.DataFrame) -> dict:
    """Pick the 80% model again if the cheapest approved model is thinly measured.

    The cost screen can crown a low point estimate that rests on a few dozen
    failures or on one or two datacenters. The 80% buy then moves to the
    approved model with the most failures, if that model is well measured
    and has actually run in the large datacenters.
    """
    primary = plan["primary"]
    alternate = plan["alternate"]
    if primary is None:
        return {"swapped": False, "reason": "no approved model"}
    lookup = table.set_index("model")
    site_days = hdd.groupby("datacenter")["drive_days"].sum()
    total_days = float(site_days.sum())
    large_sites = set(site_days[site_days >= LARGE_DATACENTER_SHARE * total_days].index)
    primary_failures = int(lookup.loc[primary["model"], "failures"])
    primary_wide = covers_large_sites(hdd, primary["model"], large_sites)
    reason = "cost screen stands"
    standard_name = primary["model"]
    second_name = None if alternate is None else alternate["model"]
    swapped = False
    if alternate is not None:
        alternate_failures = int(lookup.loc[alternate["model"], "failures"])
        alternate_wide = covers_large_sites(hdd, alternate["model"], large_sites)
        thin = primary_failures < MIN_FAILURES_FOR_STANDARD and alternate_failures >= MIN_FAILURES_WELL_MEASURED
        narrow = (not primary_wide) and alternate_wide and alternate_failures >= MIN_FAILURES_WELL_MEASURED
        if thin or narrow:
            swapped = True
            standard_name = alternate["model"]
            second_name = primary["model"]
            reason = (
                "The cheapest approved model has "
                f"{primary_failures} failures and "
                f"{'is not' if not primary_wide else 'is'} in every large datacenter. "
                f"{standard_name} has {alternate_failures} failures and is the 80% buy."
            )
    standard = lookup.loc[standard_name]
    standard_tb = MISSION_TB * (1.0 - DUAL_SOURCE_SHARE) if second_name else MISSION_TB
    standard_plan = stocking_plan(
        int(np.ceil(standard_tb / int(standard["capacity_tb"]))),
        float(standard["planning_afr"]),
        int(standard["capacity_tb"]),
        spare_budget_usd=1e18,
    )
    payload = {
        "swapped": swapped,
        "reason": reason,
        "cost_screen_model": primary["model"],
        "large_datacenters": sorted(str(site) for site in large_sites),
        "standard_model": standard_name,
        "standard_manufacturer": standard["manufacturer"],
        "standard_capacity_tb": int(standard["capacity_tb"]),
        "standard_drives": standard_plan["deployed_drives"],
        "standard_spares": standard_plan["spares_at_target"],
        "standard_afr": float(standard["afr"]),
        "standard_planning_afr": float(standard["planning_afr"]),
        "standard_basis": standard["planning_basis"],
        "standard_failures": int(standard["failures"]),
        "standard_afr_low": float(standard["afr_low"]),
        "standard_afr_high": float(standard["afr_high"]),
        "spare_cost_usd": standard_plan["spare_cost_usd"],
        "zero_spare_shortfall_tb": standard_plan["zero_spare_shortfall_tb"],
        "second_model": None,
    }
    if second_name is not None:
        second = lookup.loc[second_name]
        second_tb = MISSION_TB * DUAL_SOURCE_SHARE
        second_plan = stocking_plan(
            int(np.ceil(second_tb / int(second["capacity_tb"]))),
            float(second["planning_afr"]),
            int(second["capacity_tb"]),
            spare_budget_usd=1e18,
        )
        payload.update(
            {
                "second_model": second_name,
                "second_manufacturer": second["manufacturer"],
                "second_capacity_tb": int(second["capacity_tb"]),
                "second_drives": second_plan["deployed_drives"],
                "second_spares": second_plan["spares_at_target"],
                "second_afr": float(second["afr"]),
                "second_planning_afr": float(second["planning_afr"]),
                "second_basis": second["planning_basis"],
                "second_failures": int(second["failures"]),
                "second_afr_low": float(second["afr_low"]),
                "second_afr_high": float(second["afr_high"]),
                "spare_cost_usd": standard_plan["spare_cost_usd"] + second_plan["spare_cost_usd"],
                "zero_spare_shortfall_tb": (
                    standard_plan["zero_spare_shortfall_tb"] + second_plan["zero_spare_shortfall_tb"]
                ),
            }
        )
    return payload


def names(table: pd.DataFrame, label: str) -> list[str]:
    return table.loc[table["decision"] == label, "model"].tolist()


def decision_delta(base: pd.DataFrame, other: pd.DataFrame) -> dict:
    base_stop = set(names(base, "stop"))
    other_stop = set(names(other, "stop"))
    return {
        "stop_added": sorted(other_stop - base_stop),
        "stop_removed": sorted(base_stop - other_stop),
    }


def funnel(drives: pd.DataFrame, table: pd.DataFrame) -> list[dict]:
    summary_path = REPORTS / "process_summary.json"
    summary = json.loads(summary_path.read_text()) if summary_path.exists() else None
    if summary is None:
        loaded = int(drives["snapshot_rows"].sum())
        dropped = 0
        rejected = 0
    else:
        loaded = sum(item["raw_rows_loaded"] for item in summary["quarters"])
        dropped = sum(item["dropped_identity_rows"] for item in summary["quarters"])
        rejected = sum(item["rejected_rows"] for item in summary["quarters"])
    hdd = drives[drives["media"] == "HDD"]
    gated = table[table["decision"].isin(["approved", "stop", "watch"])]
    return [
        {"step": "Daily snapshot rows loaded", "rows": loaded + rejected},
        {"step": "Rows the parser rejected", "rows": rejected},
        {"step": "Rows with a serial, model, and date", "rows": loaded - dropped},
        {"step": "Drive-days on or before failure", "rows": int(drives["drive_days"].sum())},
        {"step": "Drives (serial and model)", "rows": int(len(drives))},
        {"step": "Hard drives", "rows": int(len(hdd))},
        {"step": "Hard drives with usable power-on age", "rows": int(hdd["age_ok"].sum())},
        {"step": "Current models with enough evidence to judge", "rows": int(gated["model"].nunique())},
    ]


def curve_series(drives: pd.DataFrame, labels: list[str]) -> list[dict]:
    series = []
    hdd = drives[(drives["media"] == "HDD") & drives["age_ok"]]
    series.append(("All judged hard drives", hdd))
    for label in labels:
        series.append((label, hdd[hdd["model"] == label]))
    # The first series is the whole age-ok HDD set, which is the right
    # population for the fleet curve. "Judged" is filtered below if needed.
    series[0] = ("All hard drives with usable age", hdd)
    return kaplan_meier(drives, series)


def main() -> None:
    drive_path = DATA_PROCESSED / "drives.parquet"
    if not drive_path.exists():
        raise FileNotFoundError("data/processed/drives.parquet is missing. Run analysis/process_data.py first.")
    drives, units = apply_age(pd.read_parquet(drive_path))
    drives, aliases = canonicalize_models(drives)
    hdd = drives[drives["media"] == "HDD"].copy()
    latest = pd.read_parquet(
        DATA_PROCESSED / "quarters" / f"{LATEST_QUARTER}.parquet",
        columns=["model"],
    )
    present = set(latest["model"].dropna().unique())
    table, fleet = build_model_table(hdd, present)
    table = attach_planning(table, hdd)
    approved = table[table["decision"] == "approved"].copy()
    plan = optimize_standard(approved)
    operational = operational_buy(plan, table, hdd)
    tested = table[table["decision"].isin(["approved", "stop", "watch"])]
    bonferroni_alpha = 0.05 / max(len(tested), 1)
    bonferroni_table, _ = build_model_table(hdd, present, alpha=bonferroni_alpha)
    loose_table, _ = build_model_table(hdd, present, min_drives=100, min_drive_days=50_000)
    short = hdd[hdd["drive_days"] >= 7]
    short_table, _ = build_model_table(short, present)
    loose_plan = optimize_standard(loose_table[loose_table["decision"] == "approved"])
    short_plan = optimize_standard(short_table[short_table["decision"] == "approved"])

    age_rows = hdd[hdd["age_ok"]]
    bands = life_table(
        age_rows["entry_years"].to_numpy(),
        age_rows["exit_years"].to_numpy(),
        age_rows["failed"].to_numpy(dtype=bool),
    )
    baseline = baseline_hazard(bands)
    rise = sharp_rise(bands, baseline)
    infant = infant_band(bands, baseline)

    cox_frame = prepare_cox_frame(drives)
    cox = fit_cox(cox_frame, formula_column="mfr")
    calendar_cox = fit_calendar_cox(drives)
    action_models = names(table, "stop")
    if plan["primary"] is not None:
        action_models = [plan["primary"]["model"]] + [
            model for model in action_models if model != plan["primary"]["model"]
        ]
    if plan["alternate"] is not None:
        action_models.append(plan["alternate"]["model"])
    # Model-level Cox uses the action set plus the largest approved models,
    # and only groups with at least 20 failures.
    failure_by_model = table.set_index("model")["failures"]
    cox_models = [
        model
        for model in list(dict.fromkeys(action_models + approved["model"].tolist()))
        if int(failure_by_model.get(model, 0)) >= 20
    ][:8]
    model_reference = None
    if cox_models:
        model_reference = table[table["model"].isin(cox_models)].sort_values("drives", ascending=False).iloc[0]["model"]
    model_cox = fit_cox(
        cox_frame[cox_frame["model"].isin(cox_models)].assign(model=lambda frame: frame["model"]),
        formula_column="model",
        reference=None if model_reference is None else str(model_reference),
    ) if len(cox_models) >= 2 else {"fit": False, "reason": "fewer than two models with 20 failures"}

    primary_name = operational.get("standard_model")
    curve_labels = []
    for label in [primary_name, operational.get("second_model"), *names(table, "stop")]:
        if label and label not in curve_labels:
            curve_labels.append(label)
    curves = curve_series(drives, curve_labels[:5])

    scale = []
    if operational.get("standard_model"):
        scale = scale_table(
            float(operational["standard_planning_afr"]),
            int(operational["standard_capacity_tb"]),
        )
    sensitivity_plans = []
    if operational.get("standard_model"):
        for lead in SENSITIVITY_LEAD_DAYS:
            for service in SENSITIVITY_SERVICE:
                sensitivity_plans.append(
                    stocking_plan(
                        int(operational["standard_drives"]),
                        float(operational["standard_planning_afr"]),
                        int(operational["standard_capacity_tb"]),
                        lead_days=lead,
                        service=service,
                        spare_budget_usd=1e18,
                    )
                )

    window_start = str(pd.to_datetime(hdd["first_seen"]).min().date())
    window_end = str(pd.to_datetime(hdd["last_seen"]).max().date())
    results = {
        "window": {"start": window_start, "end": window_end, "quarters": list(QUARTERS)},
        "counts": {
            "drives": int(len(drives)),
            "hdd_drives": int(len(hdd)),
            "ssd_drives": int((drives["media"] == "SSD").sum()),
            "snapshot_rows": int(drives["snapshot_rows"].sum()),
            "drive_days": int(drives["drive_days"].sum()),
            "failures": int(drives["failed"].sum()),
            "age_ok_hdd": int(hdd["age_ok"].sum()),
        },
        "fleet": fleet,
        "models": table.to_dict(orient="records"),
        "manufacturers": group_rate(hdd, "manufacturer"),
        "capacities": group_rate(hdd[hdd["capacity_tb"].notna()], "capacity_tb"),
        "quarterly": quarterly_rates(hdd),
        "datacenter": datacenter_rates(hdd, primary_name),
        "plan": plan,
        "operational": operational,
        "model_aliases": aliases,
        "life_table": bands.to_dict(orient="records"),
        "baseline": baseline,
        "sharp_rise": rise,
        "infant": infant,
        "cox": cox,
        "calendar_cox": calendar_cox,
        "model_cox": model_cox,
        "curves": curves,
        "scale": scale,
        "sensitivity": {
            "bonferroni_alpha": bonferroni_alpha,
            "bonferroni": decision_delta(table, bonferroni_table),
            "lower_evidence_gate": {
                **decision_delta(table, loose_table),
                "primary": None if loose_plan["primary"] is None else loose_plan["primary"]["model"],
            },
            "drop_drives_seen_under_7_days": {
                **decision_delta(table, short_table),
                "primary": None if short_plan["primary"] is None else short_plan["primary"]["model"],
                "drives_removed": int((hdd["drive_days"] < 7).sum()),
            },
            "lead_time_and_service": sensitivity_plans,
        },
        "smart9_units": units.to_dict(orient="records"),
        "funnel": funnel(drives, table),
        "baseline_primary": None if plan["primary"] is None else plan["primary"]["model"],
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    payload = clean(results)
    (REPORTS / "results.json").write_text(json.dumps(payload, indent=2))
    table.to_csv(REPORTS / "model_afr.csv", index=False)
    units.to_csv(REPORTS / "smart9_units.csv", index=False)
    bands.to_csv(REPORTS / "life_table.csv", index=False)
    render_figures(payload)
    render_memo(payload)
    render_dashboard(payload)
    print(
        f"fleet AFR {fleet['afr']:.4f}; standard "
        f"{operational.get('standard_model')}; sharp rise {rise['found']} at {rise['age_years']}",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 — top-level CLI boundary
        print(f"analyze failed: {exc}", file=sys.stderr)
        raise
