"""One-page HTML dashboard. Each section title is a question from the brief."""

from __future__ import annotations

import html
from pathlib import Path

from analysis.assumptions import DASHBOARD, FIGURES, LEAD_TIME_DAYS, MISSION_TB, SERVICE_LEVEL, SPARE_BUDGET_USD


def pct(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{100 * float(value):.{digits}f}%"


def num(value) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(float(value))):,}"


def money(value) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.0f}"


def cell(value: str) -> str:
    return f"<td>{html.escape(value)}</td>"


def model_rows(results: dict) -> str:
    wanted = {"approved", "stop", "watch"}
    rows = [row for row in results["models"] if row["decision"] in wanted]
    rows.sort(key=lambda row: (row["decision"] != "stop", row["afr"] if row["afr"] is not None else 9))
    operational = results.get("operational") or {}
    primary = operational.get("standard_model")
    alternate = operational.get("second_model")
    body = []
    for row in rows:
        if row["model"] == primary:
            label = "Standardize"
        elif row["model"] == alternate:
            label = "Second source"
        elif row["decision"] == "stop":
            label = "Stop buying"
        elif row["decision"] == "watch":
            label = "Do not standardize"
        else:
            label = "Approved"
        body.append(
            "<tr>"
            + cell(row["model"])
            + cell(row["manufacturer"])
            + cell(str(int(row["capacity_tb"])) if row["capacity_tb"] else "—")
            + cell(pct(row["afr"]))
            + cell(f"{pct(row['afr_low'])}–{pct(row['afr_high'])}")
            + cell(num(row["drives"]))
            + cell(label)
            + "</tr>"
        )
    return "\n".join(body)


def scale_rows(results: dict) -> str:
    body = []
    for row in results["scale"]:
        body.append(
            "<tr>"
            + cell(num(row["deployed_drives"]))
            + cell(f"{row['mean_failures']:.1f}")
            + cell(num(row["spares"]))
            + cell(f"{row['zero_spare_shortfall_tb']:.0f} TB")
            + "</tr>"
        )
    return "\n".join(body)


def figure(name: str, alt: str) -> str:
    path = FIGURES / name
    if not path.exists():
        return ""
    # Relative to dashboard/index.html so the page opens without a server.
    return f'<img src="../docs/figures/{html.escape(name)}" alt="{html.escape(alt)}">'


def render_dashboard(results: dict) -> Path:
    operational = results.get("operational") or {}
    rise = results["sharp_rise"]
    if rise.get("found"):
        age_value = f"{rise['age_years']:.1f} yr"
        age_note = f"Failure risk first doubles the age 1–3 hazard at {rise['age_years']:.1f} power-on years."
    elif rise.get("peak_age_years") is not None:
        age_value = f"{rise['peak_age_years']:.1f} yr"
        age_note = "Highest well-estimated hazard. No sharp doubling."
    else:
        age_value = "n/a"
        age_note = "Not enough exposure to name an age"
    if not operational.get("standard_model"):
        spare_value = "n/a"
        spare_note = "No model cleared the buy rule"
        standard = "None"
    else:
        standard = operational["standard_model"]
        if operational.get("second_model"):
            spare_value = num(operational["standard_spares"] + operational["second_spares"])
            spare_note = (
                f"{num(operational['standard_spares'])} {operational['standard_model']} + "
                f"{num(operational['second_spares'])} {operational['second_model']}"
            )
        else:
            spare_value = num(operational["standard_spares"])
            spare_note = f"For {num(operational['standard_drives'])} deployed drives"
    stops = [row["model"] for row in results["models"] if row["decision"] == "stop"]
    stop_text = f"{len(stops)} models" if stops else "none"
    DASHBOARD.mkdir(parents=True, exist_ok=True)
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Drive reliability for the 2027 buy</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #1c1917;
      --muted: #5c6b73;
      --line: #e7e5e4;
      --paper: #f6f4f1;
      --card: #ffffff;
      --blue: #1f4e79;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: Palatino, "Palatino Linotype", "Iowan Old Style", Georgia, serif;
    }}
    main {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 28px 22px 48px;
    }}
    header p {{
      margin: 0;
      color: var(--blue);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 4px 0 6px;
      font-size: 28px;
      font-weight: 600;
      line-height: 1.2;
    }}
    header .window {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .kpis {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
      margin-top: 18px;
    }}
    .kpi {{
      background: var(--card);
      border: 1px solid var(--line);
      padding: 12px 14px;
    }}
    .kpi .label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
    }}
    .kpi .value {{
      display: block;
      margin-top: 4px;
      font-size: 22px;
      line-height: 1.2;
      font-variant-numeric: tabular-nums;
    }}
    .kpi .value.model {{
      font-size: 14px;
      letter-spacing: -0.01em;
    }}
    .kpi .note {{
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
    }}
    section {{
      margin-top: 26px;
    }}
    h2 {{
      margin: 0 0 6px;
      font-size: 18px;
      font-weight: 600;
    }}
    .answer {{
      margin: 0 0 10px;
      font-size: 15px;
      line-height: 1.4;
    }}
    img {{
      width: 100%;
      height: auto;
      background: white;
      border: 1px solid var(--line);
    }}
    .table-scroll {{
      overflow-x: auto;
      background: white;
      border: 1px solid var(--line);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: white;
      font-size: 13px;
      font-variant-numeric: tabular-nums;
    }}
    th, td {{
      text-align: left;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{
      font-size: 12px;
      color: var(--muted);
      font-weight: 600;
    }}
    footer {{
      margin-top: 22px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }}
    @media (max-width: 800px) {{
      .kpis {{ grid-template-columns: 1fr 1fr; }}
      h1 {{ font-size: 24px; }}
    }}
  </style>
</head>
<body>
<main>
  <header>
    <p>2027 capacity buy</p>
    <h1>Drive reliability and spares</h1>
    <p class="window">Backblaze Drive Stats, {html.escape(results['window']['start'])} to {html.escape(results['window']['end'])}. Hard drives only. The site plan is fictional.</p>
  </header>
  <div class="kpis">
    <div class="kpi">
      <span class="label">Fleet annualized failure rate</span>
      <span class="value">{pct(results['fleet']['afr'])}</span>
      <span class="note">95% interval {pct(results['fleet']['afr_low'])}–{pct(results['fleet']['afr_high'])}</span>
    </div>
    <div class="kpi">
      <span class="label">Standardize on</span>
      <span class="value model">{html.escape(standard)}</span>
      <span class="note">Stop buying: {html.escape(stop_text)}</span>
    </div>
    <div class="kpi">
      <span class="label">Age where risk rises</span>
      <span class="value">{html.escape(age_value)}</span>
      <span class="note">{html.escape(age_note)}</span>
    </div>
    <div class="kpi">
      <span class="label">Spares for the {MISSION_TB / 1000:.0f} PB plan</span>
      <span class="value">{html.escape(spare_value)}</span>
      <span class="note">{html.escape(spare_note)}. {int(SERVICE_LEVEL * 100)}% service, {LEAD_TIME_DAYS}-day lead, {money(SPARE_BUDGET_USD)} budget.</span>
    </div>
  </div>

  <section>
    <h2>Which models should we standardize on, and which should we stop buying?</h2>
    <p class="answer">Standardize on {html.escape(standard)}. Stop buying {html.escape(stop_text)}. A stop is a current model of at least 8 TB whose lower 95% bound sits above the fleet rate, with at least 200 drives and 100,000 drive-days.</p>
    {figure("afr_by_model.png", "Annualized failure rate by model with 95 percent intervals. The dashed line is the fleet rate.")}
    <div class="table-scroll">
    <table>
      <caption class="answer">Models with enough evidence to judge. The interval is a 95% Garwood interval. Approved means the rate cleared the screen. A model with only a handful of failures is not a proven buy.</caption>
      <thead>
        <tr><th>Model</th><th>Maker</th><th>TB</th><th>AFR</th><th>95% interval</th><th>Drives</th><th>Decision</th></tr>
      </thead>
      <tbody>
        {model_rows(results)}
      </tbody>
    </table>
    </div>
  </section>

  <section>
    <h2>At what age does failure risk rise sharply?</h2>
    <p class="answer">{html.escape(age_note)} From age 1 to 3 the hazard is {pct(results['baseline'].get('hazard_per_year'))} per year. It is already higher at age 3, and the highest well-estimated band is {pct(results['sharp_rise'].get('peak_hazard_per_year'))} per year at {results['sharp_rise'].get('peak_age_years')} years.</p>
    {figure("hazard_by_age.png", "Failure hazard by half-year of power-on age, with 95 percent intervals and the age 1 to 3 baseline.")}
    {figure("kaplan_meier.png", "Kaplan-Meier survival by power-on age for the fleet and the models in the decision, with left truncation.")}
  </section>

  <section>
    <h2>How many spares should we keep during replacement lead time?</h2>
    <p class="answer">{html.escape(spare_note)}. Counts are Poisson quantiles, recomputed at each fleet size, using the primary model’s planning failure rate.</p>
    {figure("spares_by_fleet_size.png", "Spare drives required for a 42-day lead time at 99 percent service, for fleet sizes from 500 to 10,000 of the primary model.")}
    <div class="table-scroll">
    <table>
      <caption class="answer">Single-model fleets of the primary drive. Expected shortfall is the terabytes lost during 42 days if the shelf is empty.</caption>
      <thead>
        <tr><th>Deployed drives</th><th>Expected failures in 42 days</th><th>Spares at 99%</th><th>Shortfall with zero spares</th></tr>
      </thead>
      <tbody>
        {scale_rows(results)}
      </tbody>
    </table>
    </div>
  </section>

  <footer>
    <p>Questions on this page are the three questions in the brief. AFR = failures / drive-days × 365. Age is SMART 9 power-on time. A drive enters the risk set at the age it already had on its first snapshot. Source: Backblaze Drive Stats, downloaded 23 October 2026. Raw data is not redistributed. Planning mission {MISSION_TB / 1000:.0f} PB, lead time {LEAD_TIME_DAYS} days, service level {int(SERVICE_LEVEL * 100)}%, spare budget {money(SPARE_BUDGET_USD)}. These describe Backblaze’s fleet and are not a causal claim about another workload.</p>
  </footer>
</main>
</body>
</html>
"""
    path = DASHBOARD / "index.html"
    path.write_text(page)
    return path
