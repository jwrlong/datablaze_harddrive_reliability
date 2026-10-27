# Drive reliability and spares planning

A fictional infrastructure director needs a 2027 buy list, a replacement age, and a spare count. On Backblaze's fleet from 1 January 2025 through 30 June 2026, standardize on the WDC 22 TB `WUH722222ALE6L4` (AFR 0.47% on 282 failures), stop 13 current models, and treat 4.0 power-on years as the point where failure risk doubles.

## Scenario

The director is buying hard drives without a reliability screen and setting the spare shelf by guesswork. The decision is next year's capacity budget: which models to standardize on, when power-on age makes replacement the default, and how many spares cover a 42-day lead time for a 100 PB raw refresh. The stakeholder and the site are fictional. The drives are Backblaze's.

## Key findings

1. **Buy the WDC 22 TB `WUH722222ALE6L4` for 80% of new terabytes.** Its AFR is 0.47% (95% interval 0.42–0.53%) on 45,893 drives and 282 failures, and it has run in every large datacenter. Keep the Toshiba 24 TB `MG11ACA24TE` as the 20% second source (0.50% on 23 failures). Stop 13 current models of at least 8 TB whose lower 95% bound sits above the fleet AFR of 1.40%, including the 24 TB Seagate `ST24000NM002H` at 3.04%.
2. **Failure risk doubles the age 1–3 hazard at 4.0 power-on years.** That baseline is 0.68% per year. The hazard is already 1.33% at age 3.0 and reaches 7.1% per year at 8.5 years. The first half-year is also elevated, at 0.98% per year.
3. **Stock 7 WDC spares and 3 Toshiba spares** for 3,637 and 834 deployed drives, a 42-day lead, and 99% service. The spare buy is $3,164 against a $100,000 spare budget. An empty shelf leaves about 68 TB short over those 42 days.
4. **The latest quarter is hotter than the window.** Hard-drive AFR in Q2 2026 is 1.75%, against 1.12% in Q4 2025 and 1.40% over the full 18 months. The standard model's own Q2 rate is 0.55%, so the jump is not that model.

## Deliverables

- [Two-page memo](reports/recommendation_memo.pdf)
- [Dashboard](dashboard/index.html)
- Case study: [Ask](docs/01_ask.md), [Prepare](docs/02_prepare.md), [Process](docs/03_process.md), [Analyze](docs/04_analyze.md), [Share](docs/05_share.md), [Act](docs/06_act.md)
- Code: `sql/`, `analysis/`

## Tools

Python 3.12, DuckDB 1.5.6 for the SQL collapse, pandas, lifelines, scipy, matplotlib, and reportlab. Package pins are in `requirements.txt`.

## How to reproduce

Raw zips are downloaded into `data/raw/` and are not in git.

1. `python3 -m pip install -r requirements.txt`
2. `python3 analysis/download_data.py`
3. `python3 analysis/process_data.py`
4. `python3 analysis/analyze.py`

`python3 -m unittest tests.test_metrics tests.test_collapse` checks the rate, spare, and collapse logic without the zips.

## Limitations

The rates describe Backblaze's data-center fleet, workload, and replacement practice. They are a civilian analog of a sustainment problem, and they may not generalize. Model differences are associations. Failure is Backblaze's removal flag. The 100 PB mission, 42-day lead, 99% service level, $100,000 spare budget, and the $/TB prices are planning assumptions for this fictional decision.
