"""Two-page memo for the infrastructure director."""

from __future__ import annotations

import re
from pathlib import Path

from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analysis.assumptions import (
    AFR_TOLERANCE,
    DUAL_SOURCE_SHARE,
    FIGURES,
    LEAD_TIME_DAYS,
    MIN_CAPACITY_TB,
    MIN_DRIVE_DAYS,
    MIN_DRIVES,
    MISSION_TB,
    REPORTS,
    SERVICE_LEVEL,
    SPARE_BUDGET_USD,
)

INK = HexColor("#1C1917")
MUTED = HexColor("#5C6B73")
RULE = HexColor("#E7E5E4")
BLUE = HexColor("#1F4E79")
BAND = HexColor("#F4F7FA")


def pct(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{100 * float(value):.{digits}f}%"


def money(value) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):,.0f}"


def num(value) -> str:
    if value is None:
        return "n/a"
    return f"{int(round(float(value))):,}"


def styles() -> dict[str, ParagraphStyle]:
    return {
        "kicker": ParagraphStyle("kicker", fontName="Times-Bold", fontSize=8, textColor=BLUE, spaceAfter=2),
        "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=14, leading=17, textColor=INK, spaceAfter=4),
        "meta": ParagraphStyle("meta", fontName="Times-Roman", fontSize=8.5, leading=11, textColor=INK, spaceAfter=1),
        "h": ParagraphStyle("h", fontName="Times-Bold", fontSize=11, leading=13, textColor=INK, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=9, leading=11.5, textColor=INK, alignment=TA_JUSTIFY, spaceAfter=4),
        "small": ParagraphStyle("small", fontName="Times-Roman", fontSize=8, leading=10, textColor=INK, alignment=TA_LEFT, spaceAfter=2),
        "footer": ParagraphStyle("footer", fontName="Times-Roman", fontSize=7.5, leading=9.5, textColor=MUTED, spaceBefore=4),
        "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=7.5, leading=9, textColor=INK),
        "head": ParagraphStyle("head", fontName="Times-Bold", fontSize=7.5, leading=9, textColor=INK),
    }


def page_chrome(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    canvas.line(0.6 * inch, 0.42 * inch, letter[0] - 0.6 * inch, 0.42 * inch)
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.6 * inch, 0.28 * inch, "Fictional site plan  ·  Evidence from Backblaze Drive Stats  ·  Not a Backblaze budget")
    canvas.drawRightString(letter[0] - 0.6 * inch, 0.28 * inch, str(doc.page))
    canvas.restoreState()


def model_map(results: dict) -> dict[str, dict]:
    return {row["model"]: row for row in results["models"]}


def plain_decision(row: dict, results: dict) -> str:
    operational = results.get("operational") or {}
    if row["model"] == operational.get("standard_model"):
        return "Standardize"
    if row["model"] == operational.get("second_model"):
        return "Second source"
    return {
        "stop": "Stop buying",
        "watch": "Do not standardize",
        "approved": "Approved",
        "legacy_capacity": "Legacy size",
        "insufficient_evidence": "Too little data",
        "not_in_current_fleet": "Not in latest quarter",
    }.get(row["decision"], row["decision"])


def action_rows(results: dict) -> list[dict]:
    operational = results.get("operational") or {}
    ordered = [operational.get("standard_model"), operational.get("second_model")]
    stops = [row for row in results["models"] if row["decision"] == "stop"]
    stops.sort(key=lambda row: row["afr"] or 0, reverse=True)
    ordered.extend(row["model"] for row in stops[:6])
    lookup = model_map(results)
    chosen = []
    seen = set()
    for name in ordered:
        if not name or name in seen or name not in lookup:
            continue
        seen.add(name)
        chosen.append(lookup[name])
    return chosen


def age_sentence(results: dict) -> str:
    rise = results["sharp_rise"]
    base = results["baseline"]
    if not rise.get("found"):
        return "The age curve does not clear the doubling rule inside the ages with enough evidence."
    sentence = (
        f"The age 1–3 hazard is {pct(base['hazard_per_year'])} per year. "
        f"It first doubles that rate at {rise['age_years']:.1f} power-on years "
        f"({pct(rise['hazard_per_year'])} per year)."
    )
    peak_age = rise.get("peak_age_years")
    peak_hazard = rise.get("peak_hazard_per_year")
    if peak_age is not None and peak_hazard is not None and peak_age > rise["age_years"]:
        sentence += f" It keeps climbing, to {pct(peak_hazard)} per year at {peak_age:.1f} years."
    return sentence


def infant_sentence(results: dict) -> str:
    infant = results["infant"]
    if not infant.get("elevated"):
        return "The first half-year is not above the age 1–3 baseline on this rule."
    return (
        f"The first half-year is also elevated, at {pct(infant.get('hazard_per_year'))} per year, "
        "so new drives need spares before wear-out begins."
    )


def spare_sentence(results: dict) -> str:
    operational = results.get("operational") or {}
    if not operational.get("standard_model"):
        return "No model cleared the buy rule, so there is no spare count to approve."
    mission = f"{MISSION_TB / 1000:.0f} PB"
    if operational.get("second_model"):
        return (
            f"For a {mission} raw refresh, buy {100 * (1 - DUAL_SOURCE_SHARE):.0f}% of the terabytes as "
            f"{operational['standard_model']} ({num(operational['standard_drives'])} drives) and "
            f"{100 * DUAL_SOURCE_SHARE:.0f}% as {operational['second_model']} "
            f"({num(operational['second_drives'])} drives). "
            f"Stock {num(operational['standard_spares'])} and {num(operational['second_spares'])} spares "
            f"for a {LEAD_TIME_DAYS}-day lead time at a {pct(SERVICE_LEVEL, 0)} service level. "
            f"The spare buy is {money(operational['spare_cost_usd'])}, inside the {money(SPARE_BUDGET_USD)} spare budget. "
            f"With an empty shelf, expected shortfall over that lead time is "
            f"{operational['zero_spare_shortfall_tb']:.0f} TB."
        )
    return (
        f"For a {mission} raw refresh, field {num(operational['standard_drives'])} "
        f"{operational['standard_model']} drives and stock {num(operational['standard_spares'])} spares."
    )


def bottom_line(results: dict) -> str:
    operational = results.get("operational") or {}
    stops = [row for row in results["models"] if row["decision"] == "stop"]
    stops.sort(key=lambda row: row["afr"] or 0, reverse=True)
    if not operational.get("standard_model"):
        opening = "No current model cleared the evidence and reliability rules, so this memo does not name a standard drive."
    else:
        opening = (
            f"Standardize the 2027 capacity buy on {operational['standard_model']} "
            f"({operational['standard_manufacturer']}, {int(operational['standard_capacity_tb'])} TB, "
            f"AFR {pct(operational['standard_afr'])} on {num(operational['standard_failures'])} failures)."
        )
        if operational.get("second_model"):
            opening += (
                f" Keep {operational['second_model']} as the second source "
                f"({pct(operational['second_afr'])} on {num(operational['second_failures'])} failures), at 20% of terabytes."
            )
    if stops:
        worst = stops[0]
        seagate_24 = next((row for row in stops if row["model"] == "ST24000NM002H"), None)
        named = worst["model"]
        extra = ""
        if seagate_24 and seagate_24["model"] != named:
            extra = f", including the 24 TB Seagate {seagate_24['model']} at {pct(seagate_24['afr'])}"
        stop_text = f" Stop buying {len(stops)} current models whose lower 95% bound is above the fleet rate, led by {named} at {pct(worst['afr'])}{extra}."
    else:
        stop_text = " No current model of at least 8 TB is reliably worse than the fleet."
    return opening + stop_text + " " + age_sentence(results)


def ph_sentence(results: dict) -> str:
    age_cox = results["cox"]
    calendar = results.get("calendar_cox") or {}
    age_note = (
        "The age-scale Cox model cannot take a Schoenfeld test in this software once left truncation is included."
    )
    if not calendar.get("fit"):
        return age_note + " The buy list does not use hazard ratios."
    status = calendar.get("proportional_hazards_status")
    if status == "holds":
        tested = "A second Cox model, on time since the first snapshot, with power-on age at entry as a covariate, passed the proportional-hazards test."
    elif status == "violated":
        tested = "A second Cox model, on time since the first snapshot, failed the proportional-hazards test, so those hazard ratios are not used."
    else:
        tested = "A proportional-hazards test on the follow-up-time model could not be completed."
    return age_note + " " + tested + " The buy list comes from the annualized rates and the life table."


def recommendation_items(results: dict) -> list[str]:
    operational = results.get("operational") or {}
    items = []
    if not operational.get("standard_model"):
        items.append("Do not name a standard model until a candidate clears the evidence and reliability rules.")
    else:
        items.append(
            f"Approve {operational['standard_model']} for 80% of new terabytes and "
            f"{operational.get('second_model') or 'no second model'} for 20%."
        )
        if operational.get("second_model"):
            items.append(
                f"Stock {num(operational['standard_spares'])} {operational['standard_model']} spares and "
                f"{num(operational['second_spares'])} {operational['second_model']} spares "
                f"({LEAD_TIME_DAYS} days, {pct(SERVICE_LEVEL, 0)} service)."
            )
        else:
            items.append(
                f"Stock {num(operational['standard_spares'])} spares "
                f"({LEAD_TIME_DAYS} days, {pct(SERVICE_LEVEL, 0)} service)."
            )
    stops = [row["model"] for row in results["models"] if row["decision"] == "stop"]
    if stops:
        items.append(f"Stop new purchases of {len(stops)} models, listed on the dashboard. Do not buy ST24000NM002H as a 24 TB alternative.")
    rise = results["sharp_rise"]
    if rise.get("found"):
        items.append(
            f"Put drives at or above {rise['age_years']:.1f} power-on years on the replacement list. Use power-on age, not the date the drive first appears in a report."
        )
    items.append("If the fleet or the lead time changes, recompute the Poisson quantile at the new size. Do not scale the spare count by dividing.")
    return items


def build_story(results: dict, style: dict, *, include_spare_chart: bool) -> list:
    story = []
    story.append(Paragraph("BUDGET MEMO", style["kicker"]))
    story.append(Paragraph("Drive models, replacement age, and spares for the 2027 buy", style["title"]))
    for line in (
        "To: Infrastructure Director",
        "From: Reliability analysis",
        "Date: 23 October 2026",
        "Re: What to buy, when failure risk rises, and how many spares to hold",
    ):
        story.append(Paragraph(line, style["meta"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(bottom_line(results), style["body"]))

    story.append(Paragraph("1. What to buy, and what to stop buying", style["h"]))
    operational = results.get("operational") or {}
    story.append(
        Paragraph(
            f"Hard-drive AFR from {results['window']['start']} to {results['window']['end']} is "
            f"{pct(results['fleet']['afr'])} (95% interval {pct(results['fleet']['afr_low'])} to {pct(results['fleet']['afr_high'])}), "
            f"on {num(results['fleet']['failures'])} failures and {num(results['fleet']['drive_days'])} drive-days. "
            f"A model is judged with at least {MIN_DRIVES} drives, {MIN_DRIVE_DAYS:,} drive-days, {MIN_CAPACITY_TB} TB, and a drive still present in {results['window']['quarters'][-1]}. "
            f"Stop means the lower 95% bound is above the fleet rate. A buy candidate’s point AFR also has to stay within {pct(AFR_TOLERANCE - 1, 0)} of the fleet. "
            f"The cost screen’s cheapest approved model was {operational.get('cost_screen_model', 'not recorded')}. "
            f"{operational.get('reason', '')}",
            style["body"],
        )
    )
    header = ["Model", "TB", "AFR", "95% interval", "Drives", "Decision"]
    data = [[Paragraph(item, style["head"]) for item in header]]
    for row in action_rows(results):
        data.append(
            [
                Paragraph(row["model"], style["cell"]),
                Paragraph(str(int(row["capacity_tb"])) if row["capacity_tb"] else "—", style["cell"]),
                Paragraph(pct(row["afr"]), style["cell"]),
                Paragraph(f"{pct(row['afr_low'])}–{pct(row['afr_high'])}", style["cell"]),
                Paragraph(num(row["drives"]), style["cell"]),
                Paragraph(plain_decision(row, results), style["cell"]),
            ]
        )
    table = Table(data, colWidths=[1.7 * inch, 0.45 * inch, 0.6 * inch, 1.35 * inch, 0.75 * inch, 1.15 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.4, BLUE),
                ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
                ("LINEBELOW", (0, -1), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("BACKGROUND", (0, 1), (-1, 1), white),
            ]
        )
    )
    story.append(table)
    n_stops = sum(1 for row in results["models"] if row["decision"] == "stop")
    if n_stops > 6:
        story.append(Paragraph(f"The table shows the six highest stop-list rates. {n_stops - 6} more stops are on the dashboard.", style["small"]))

    story.append(Paragraph("2. When failure risk rises", style["h"]))
    story.append(Paragraph(age_sentence(results) + " " + infant_sentence(results), style["body"]))
    hazard = FIGURES / "hazard_by_age.png"
    if hazard.exists():
        story.append(Image(str(hazard), width=7.2 * inch, height=2.5 * inch, kind="proportional"))
    story.append(
        Paragraph(
            "Age is power-on years from SMART 9, not the date a drive first appears in the file. "
            "Drives that are already old on their first snapshot are left-truncated: they count only from the age at which we can see them.",
            style["small"],
        )
    )
    return story


def build_page_two(results: dict, style: dict, *, include_spare_chart: bool) -> list:
    story = []
    story.append(Paragraph("3. How many spares to hold", style["h"]))
    story.append(Paragraph(spare_sentence(results), style["body"]))
    operational = results.get("operational") or {}
    if operational.get("standard_model"):
        story.append(
            Paragraph(
                f"Spares for {operational['standard_model']} use its {operational['standard_basis']} "
                f"({pct(operational['standard_planning_afr'])}), against an all-age AFR of {pct(operational['standard_afr'])}. "
                "Planning prices are about $14 to $22 per TB, lower for larger drives, and are not quotes. "
                f"The mission is {MISSION_TB / 1000:.0f} PB raw and the spare budget is {money(SPARE_BUDGET_USD)}.",
                style["body"],
            )
        )
    if results["scale"]:
        header = ["Deployed drives", "Expected failures in 42 days", "Spares at 99%", "Shortfall with zero spares"]
        data = [[Paragraph(item, style["head"]) for item in header]]
        for row in results["scale"]:
            data.append(
                [
                    Paragraph(num(row["deployed_drives"]), style["cell"]),
                    Paragraph(f"{row['mean_failures']:.1f}", style["cell"]),
                    Paragraph(num(row["spares"]), style["cell"]),
                    Paragraph(f"{row['zero_spare_shortfall_tb']:.0f} TB", style["cell"]),
                ]
            )
        scale = Table(data, colWidths=[1.4 * inch, 2.0 * inch, 1.3 * inch, 2.1 * inch])
        scale.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BAND),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.4, BLUE),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.2, RULE),
                    ("LINEBELOW", (0, -1), (-1, -1), 0.4, RULE),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 3),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        story.append(Paragraph("Single-model spare counts for the primary drive. Each row is recomputed.", style["small"]))
        story.append(scale)
    if include_spare_chart and (FIGURES / "spares_by_fleet_size.png").exists():
        story.append(Spacer(1, 4))
        story.append(Image(str(FIGURES / "spares_by_fleet_size.png"), width=7.2 * inch, height=2.2 * inch, kind="proportional"))
    leads = [
        row
        for row in results["sensitivity"]["lead_time_and_service"]
        if abs(row["service_target"] - 0.99) < 1e-9
    ]
    if leads:
        bits = [f"{row['lead_days']} days needs {num(row['spares_at_target'])}" for row in leads]
        story.append(Paragraph("Recommended primary-model quantity, 99% service: " + "; ".join(bits) + ".", style["small"]))

    story.append(Paragraph("Recommendation", style["h"]))
    for item in recommendation_items(results):
        story.append(Paragraph("• " + item, style["body"]))

    story.append(Paragraph("What this does not tell you", style["h"]))
    datacenter = results["datacenter"]["note"]
    limits = [
        "These are Backblaze data-center drives under Backblaze’s workload and replacement practice. The rates describe that fleet. They are not a lab test, and they are not a claim about another company’s rooms, temperature, or duty cycle.",
        "Model differences are associations. Backblaze chooses which model goes into which pod. " + datacenter,
        ph_sentence(results),
        "A drive that disappears without failure = 1 is treated as censored, which is right if it was migrated and wrong if the death was never flagged. SMART 9 units were checked per model; drives with an unusable counter are in the AFR and out of the age curve.",
        "The 100 PB mission, the 42-day lead time, the 99% service level, the $100,000 spare budget, and the planning prices are assumptions for this fictional decision. Change one of them and the spare count changes. The reliability ranking does not depend on the prices.",
    ]
    for item in limits:
        story.append(Paragraph("• " + item, style["small"]))
    story.append(
        Paragraph(
            "Method, in one line: AFR = failures / drive-days × 365, with Garwood 95% intervals. "
            "Age hazard is a half-year life table with left truncation. "
            "Spares are the smallest Poisson count whose CDF is at least 99% at mean = drives × rate × 42/365. "
            "Source: Backblaze Drive Stats, Q1 2025–Q2 2026, downloaded 23 October 2026 from the Backblaze hard drive test data page. "
            "Raw files are not in this repository. Definitions and the run order are in the README.",
            style["footer"],
        )
    )
    return story


def page_count(path: Path) -> int:
    data = path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))


def render_memo(results: dict, *, include_spare_chart: bool = True) -> Path:
    """Write the two-page PDF. Drops the spare chart if that is what spills onto page 3."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / "recommendation_memo.pdf"
    style = styles()
    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.48 * inch,
        bottomMargin=0.52 * inch,
        title="Drive models, replacement age, and spares for the 2027 buy",
        author="Reliability analysis",
    )
    story = build_story(results, style, include_spare_chart=include_spare_chart)
    story.append(PageBreak())
    story.extend(build_page_two(results, style, include_spare_chart=include_spare_chart))
    doc.build(story, onFirstPage=page_chrome, onLaterPages=page_chrome)
    pages = page_count(path)
    if pages > 2 and include_spare_chart:
        return render_memo(results, include_spare_chart=False)
    if pages > 2:
        raise RuntimeError(f"Memo is {pages} pages; the brief has to stay on two.")
    return path
