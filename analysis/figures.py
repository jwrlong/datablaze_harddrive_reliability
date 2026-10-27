"""Charts for the memo, the dashboard, and the phase notes."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "data" / "tmp"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analysis.assumptions import FIGURES

BLUE = "#1F4E79"
RUST = "#8C3A2F"
GREEN = "#1F6B4A"
GOLD = "#8A6A2F"
SLATE = "#5C6B73"
INK = "#1C1917"

DECISION_COLOR = {
    "approved": GREEN,
    "stop": RUST,
    "watch": GOLD,
    "legacy_capacity": SLATE,
    "insufficient_evidence": SLATE,
    "not_in_current_fleet": SLATE,
}


def _style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#D6D3D1",
            "axes.labelcolor": INK,
            "xtick.color": INK,
            "ytick.color": INK,
            "text.color": INK,
            "axes.grid": True,
            "grid.color": "#E7E5E4",
            "grid.linewidth": 0.6,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.titleweight": "medium",
        }
    )


def _percent(value: float | None) -> float:
    if value is None:
        return float("nan")
    return 100.0 * float(value)


def save(fig: plt.Figure, name: str) -> Path:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / name
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def forest(results: dict, *, decisions: set[str] | None, name: str, title: str) -> None:
    rows = [
        row
        for row in results["models"]
        if decisions is None or row["decision"] in decisions
    ]
    rows = sorted(rows, key=lambda row: row["afr"] if row["afr"] is not None else 999)
    if not rows:
        return
    _style()
    fleet = _percent(results["fleet"]["afr"])
    fig_h = max(2.6, 0.32 * len(rows) + 0.9)
    fig, ax = plt.subplots(figsize=(7.4, fig_h))
    y = np.arange(len(rows))
    for index, row in enumerate(rows):
        color = DECISION_COLOR.get(row["decision"], SLATE)
        ax.errorbar(
            _percent(row["afr"]),
            index,
            xerr=[[_percent(row["afr"]) - _percent(row["afr_low"])], [_percent(row["afr_high"]) - _percent(row["afr"])]],
            fmt="o",
            color=color,
            markersize=4.5,
            capsize=2,
            linewidth=1,
        )
    ax.axvline(fleet, color=BLUE, linewidth=1, linestyle="--", label=f"Fleet AFR {fleet:.2f}%")
    ax.set_yticks(y)
    ax.set_yticklabels(
        [f"{row['model']}  ({row['capacity_tb']} TB, n={row['drives']:,})" for row in rows],
        fontsize=8,
    )
    ax.set_xlabel("Annualized failure rate (%)")
    ax.set_title(title)
    ax.legend(frameon=False, loc="lower right")
    ax.set_ylim(-0.6, len(rows) - 0.4)
    fig.text(
        0.01,
        0.01,
        "Source: Backblaze Drive Stats, Q1 2025–Q2 2026. Bars are 95% Garwood intervals. n is drives.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, name)


def hazard(results: dict) -> None:
    bands = results["life_table"]
    if not bands:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.1))
    centers = [(row["age_start"] + row["age_end"]) / 2 for row in bands]
    rates = [_percent(row["hazard_per_year"]) for row in bands]
    lows = [_percent(row["hazard_low"]) for row in bands]
    highs = [_percent(row["hazard_high"]) for row in bands]
    yerr = [
        [rate - low if np.isfinite(rate) and np.isfinite(low) else 0 for rate, low in zip(rates, lows)],
        [high - rate if np.isfinite(rate) and np.isfinite(high) else 0 for rate, high in zip(rates, highs)],
    ]
    ax.errorbar(centers, rates, yerr=yerr, fmt="o", color=BLUE, markersize=4, capsize=2, linewidth=1)
    base = results["baseline"]
    if base.get("hazard_per_year"):
        ax.axhline(_percent(base["hazard_per_year"]), color=GREEN, linewidth=1, linestyle="--", label="Age 1–3 baseline")
        ax.axvspan(1.0, 3.0, color=GREEN, alpha=0.08, linewidth=0)
    rise = results["sharp_rise"]
    if rise.get("found") and rise.get("age_years") is not None:
        ax.axvline(rise["age_years"], color=RUST, linewidth=1, label=f"Sharp rise at {rise['age_years']:.1f} years")
    ax.set_xlabel("Power-on age (years)")
    ax.set_ylabel("Hazard (% per year)")
    ax.set_title("Failure hazard by power-on age, left-truncated")
    ax.legend(frameon=False)
    fig.text(
        0.01,
        0.01,
        "Source: Backblaze Drive Stats, Q1 2025–Q2 2026. Exposure is power-on years inside each half-year band. Intervals are 95% Garwood.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "hazard_by_age.png")


def kaplan(results: dict) -> None:
    curves = results["curves"]
    if not curves:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    colors = [INK, BLUE, GREEN, RUST, GOLD, SLATE]
    for index, curve in enumerate(curves):
        ax.step(
            curve["age_years"],
            curve["survival"],
            where="post",
            color=colors[index % len(colors)],
            linewidth=1.4,
            label=f"{curve['label']} ({curve['failures']} failures)",
        )
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 1.02)
    ax.set_xlabel("Power-on age (years)")
    ax.set_ylabel("Survival probability")
    ax.set_title("Kaplan–Meier survival, with left truncation")
    ax.legend(frameon=False, fontsize=7)
    fig.text(
        0.01,
        0.01,
        "Source: Backblaze Drive Stats, Q1 2025–Q2 2026. A drive enters the risk set at its age on the first snapshot.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "kaplan_meier.png")


def quarterly(results: dict) -> None:
    rows = results["quarterly"]
    if not rows:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    x = np.arange(len(rows))
    rates = [_percent(row["afr"]) for row in rows]
    lows = [_percent(row["afr_low"]) for row in rows]
    highs = [_percent(row["afr_high"]) for row in rows]
    ax.errorbar(
        x,
        rates,
        yerr=[[rate - low for rate, low in zip(rates, lows)], [high - rate for rate, high in zip(rates, highs)]],
        fmt="-o",
        color=BLUE,
        capsize=3,
        linewidth=1.2,
    )
    ax.set_xticks(x)
    ax.set_xticklabels([row["quarter"] for row in rows])
    ax.set_ylabel("Annualized failure rate (%)")
    ax.set_xlabel("Quarter")
    ax.set_title("Hard-drive annualized failure rate by quarter")
    fig.text(
        0.01,
        0.01,
        "Source: Backblaze Drive Stats. Intervals are 95% Garwood. Hard drives only.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "quarterly_afr.png")


def manufacturers(results: dict) -> None:
    rows = [row for row in results["manufacturers"] if row["drive_days"] >= 100_000]
    if not rows:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    y = np.arange(len(rows))
    for index, row in enumerate(rows):
        ax.errorbar(
            _percent(row["afr"]),
            index,
            xerr=[[_percent(row["afr"]) - _percent(row["afr_low"])], [_percent(row["afr_high"]) - _percent(row["afr"])]],
            fmt="o",
            color=BLUE,
            capsize=2,
        )
    ax.set_yticks(y)
    ax.set_yticklabels([f"{row['group']} (n={row['drives']:,})" for row in rows])
    ax.set_xlabel("Annualized failure rate (%)")
    ax.set_title("Annualized failure rate by manufacturer")
    fig.text(
        0.01,
        0.01,
        "Source: Backblaze Drive Stats, Q1 2025–Q2 2026. Manufacturers with at least 100,000 drive-days. 95% Garwood intervals.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "afr_by_manufacturer.png")


def spares(results: dict) -> None:
    rows = results["scale"]
    if not rows:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.0))
    x = np.arange(len(rows))
    ax.bar(x, [row["spares"] for row in rows], color=BLUE, width=0.7)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{row['deployed_drives']:,}" for row in rows])
    ax.set_xlabel("Deployed drives of the primary model")
    ax.set_ylabel("Spares for a 42-day lead time")
    ax.set_title("99% spare count, recomputed at each fleet size")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.22)
    fig.text(
        0.01,
        0.02,
        "Each bar is a Poisson quantile at that fleet size. It is not a linear scaling of the 1,000-drive count.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "spares_by_fleet_size.png")


def funnel(results: dict) -> None:
    rows = results["funnel"]
    if not rows:
        return
    _style()
    fig, ax = plt.subplots(figsize=(7.4, 3.4))
    values = [max(row["rows"], 1) for row in rows]
    y = np.arange(len(rows))[::-1]
    ax.barh(y, values, color=BLUE, log=True)
    ax.set_yticks(y)
    ax.set_yticklabels([row["step"] for row in rows], fontsize=8)
    for ypos, row in zip(y, rows):
        ax.text(max(row["rows"], 1) * 1.05, ypos, f"{row['rows']:,}", va="center", fontsize=8, color=INK)
    ax.set_xlabel("Count (log scale)")
    ax.set_title("What survived each preparation step")
    fig.text(
        0.01,
        0.01,
        "Drive-days and drives are not the same unit as snapshot rows. The log scale keeps the later steps visible.",
        fontsize=7,
        color=SLATE,
    )
    save(fig, "preparation_funnel.png")


def render_figures(results: dict) -> None:
    """Write every chart the memo and the case study embed."""
    action = {"approved", "stop", "watch"}
    forest(
        results,
        decisions=action,
        name="afr_by_model.png",
        title="Annualized failure rate for models with enough evidence",
    )
    memo_rows = {
        row["model"]
        for row in results["models"]
        if row["decision"] == "stop"
        or (results["plan"]["primary"] and row["model"] == results["plan"]["primary"]["model"])
        or (results["plan"]["alternate"] and row["model"] == results["plan"]["alternate"]["model"])
    }
    # The memo chart is the action set. If that set is empty, skip it.
    if memo_rows:
        subset = dict(results)
        subset["models"] = [row for row in results["models"] if row["model"] in memo_rows]
        forest(
            subset,
            decisions=None,
            name="afr_memo.png",
            title="Models to standardize or stop buying",
        )
    hazard(results)
    kaplan(results)
    quarterly(results)
    manufacturers(results)
    spares(results)
    funnel(results)
