"""Tests for the rate, life-table, and spare rules. No drive data required."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import poisson

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.metrics import (  # noqa: E402
    annualized_failure_rate,
    classify_model,
    expected_unmet,
    life_table,
    optimize_standard,
    poisson_rate_ci,
    sharp_rise,
    smart9_scale,
    spares_required,
    stocking_plan,
    baseline_hazard,
)


class RateTests(unittest.TestCase):
    def test_published_style_afr(self):
        # 1,498 failures / 31,553,350 drive-days * 365 is about 1.73%.
        afr = annualized_failure_rate(1498, 31_553_350)
        self.assertAlmostEqual(afr, 0.01733, places=4)

    def test_zero_event_interval_starts_at_zero(self):
        low, high = poisson_rate_ci(0, 1000)
        self.assertEqual(low, 0.0)
        self.assertGreater(high, 0.0)

    def test_interval_covers_the_point_rate(self):
        low, high = poisson_rate_ci(40, 10_000)
        self.assertLess(low, 40 / 10_000)
        self.assertGreater(high, 40 / 10_000)


class Smart9Tests(unittest.TestCase):
    def test_hours_minutes_seconds(self):
        self.assertEqual(smart9_scale(30_000, 0.9), ("hours", 1.0))
        self.assertEqual(smart9_scale(2_000_000, 0.9), ("minutes", 60.0))
        self.assertEqual(smart9_scale(80_000_000, 0.9), ("seconds", 3600.0))
        self.assertEqual(smart9_scale(200_000, 0.9)[0], "ambiguous")
        self.assertEqual(smart9_scale(30_000, 0.2)[0], "missing")


class ClassifyTests(unittest.TestCase):
    def _label(self, **overrides):
        base = dict(
            media="HDD",
            in_latest_quarter=True,
            drives=500,
            drive_days=200_000,
            capacity_tb=16,
            afr=0.01,
            ci_low=0.008,
            fleet_afr=0.012,
        )
        base.update(overrides)
        return classify_model(**base)

    def test_stop_only_when_lower_bound_clears_the_fleet(self):
        self.assertEqual(self._label(afr=0.02, ci_low=0.015, fleet_afr=0.012), "stop")
        self.assertEqual(self._label(afr=0.02, ci_low=0.011, fleet_afr=0.012), "watch")

    def test_small_samples_and_small_drives_are_not_stops(self):
        self.assertEqual(self._label(drives=10, afr=0.2, ci_low=0.1), "insufficient_evidence")
        self.assertEqual(self._label(capacity_tb=4, afr=0.2, ci_low=0.1), "legacy_capacity")


class SpareTests(unittest.TestCase):
    def test_quantile_matches_scipy_definition(self):
        # P(K <= 3) at mu=1 is about 0.981, so 99% needs 4.
        self.assertEqual(spares_required(1.0, 0.99), 4)
        self.assertGreaterEqual(poisson.cdf(4, 1.0), 0.99)
        self.assertLess(poisson.cdf(3, 1.0), 0.99)

    def test_expected_unmet_matches_brute_force(self):
        mu = 3.5
        spares = 2
        total = 0.0
        for k in range(0, 80):
            total += max(k - spares, 0) * poisson.pmf(k, mu)
        self.assertAlmostEqual(expected_unmet(mu, spares), total, places=6)

    def test_zero_spares_returns_the_mean(self):
        self.assertAlmostEqual(expected_unmet(4.2, 0), 4.2)

    def test_budget_binds(self):
        plan = stocking_plan(5000, 0.20, 20, lead_days=180, service=0.99, spare_budget_usd=1000)
        self.assertTrue(plan["budget_binds"])
        self.assertLess(plan["achieved_service"], 0.99)
        self.assertLessEqual(plan["spare_cost_usd"], 1000)


class OptimizeTests(unittest.TestCase):
    def test_worse_model_is_not_required_but_cheaper_reliable_model_wins_on_capital_when_service_ties(self):
        approved = pd.DataFrame(
            [
                {"model": "GOOD16", "manufacturer": "Seagate", "capacity_tb": 16, "afr": 0.008},
                {"model": "GOOD20", "manufacturer": "Toshiba", "capacity_tb": 20, "afr": 0.008},
            ]
        )
        result = optimize_standard(approved, mission_tb=100_000, spare_budget_usd=100_000)
        # Same AFR and both clear 99%. The 20 TB drive is the lower planning
        # capital for 100 PB, so it is the primary.
        self.assertEqual(result["primary"]["model"], "GOOD20")
        self.assertEqual(result["alternate"]["model"], "GOOD16")
        self.assertEqual(result["recommendation"], "dual")

    def test_no_approved_models(self):
        result = optimize_standard(pd.DataFrame(columns=["model", "manufacturer", "capacity_tb", "afr"]))
        self.assertEqual(result["recommendation"], "none")


class LifeTableTests(unittest.TestCase):
    def test_left_truncation_does_not_charge_time_before_entry(self):
        # One drive enters at age 4 and fails at age 4.25. It adds no
        # exposure before 4, and one failure in the 4.0–4.5 band.
        table = life_table(np.array([4.0]), np.array([4.25]), np.array([True]))
        early = table[table["age_end"] <= 4.0]
        self.assertEqual(early["exposure_drive_years"].sum(), 0.0)
        band = table[(table["age_start"] == 4.0)].iloc[0]
        self.assertEqual(band["failures"], 1)
        self.assertAlmostEqual(band["exposure_drive_years"], 0.25)

    def test_sharp_rise_needs_a_lower_bound_above_baseline(self):
        rows = []
        for start in np.arange(0.0, 10.0, 0.5):
            rows.append(
                {
                    "age_start": float(start),
                    "age_end": float(start + 0.5),
                    "exposure_drive_years": 1000.0,
                    "failures": 10,
                    "hazard_per_year": 0.01,
                    "hazard_low": 0.005,
                    "hazard_high": 0.02,
                }
            )
        # Age 5 doubles the baseline and its lower bound clears it.
        rows[10]["hazard_per_year"] = 0.04
        rows[10]["hazard_low"] = 0.02
        rows[10]["failures"] = 40
        table = pd.DataFrame(rows)
        base = baseline_hazard(table)
        rise = sharp_rise(table, base)
        self.assertTrue(rise["found"])
        self.assertEqual(rise["age_years"], 5.0)


if __name__ == "__main__":
    unittest.main()
