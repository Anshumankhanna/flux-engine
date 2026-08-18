import json
from datetime import datetime, timezone

INPUT_FILE = "output.json"
OUTPUT_FILE = "final_output.json"

SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def load_json(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def sort_risks(risks):
    return sorted(
        risks,
        key=lambda r: SEVERITY_ORDER.get(r.get("severity", "LOW"), 0),
        reverse=True
    )


def health_status(score):
    if score >= 80:
        return {"status": "HEALTHY", "color": "GREEN"}
    if score >= 60:
        return {"status": "WATCH", "color": "YELLOW"}
    if score >= 40:
        return {"status": "STRESSED", "color": "ORANGE"}
    return {"status": "HIGH_RISK", "color": "RED"}


def dashboard_cards(baseline):
    ratios = baseline.get("ratios", {})
    liquidity = ratios.get("liquidity", {})
    leverage = ratios.get("leverage", {})
    profitability = ratios.get("profitability", {})
    wc = ratios.get("working_capital", {})
    cash = ratios.get("cash_flow", {})

    return [
        {"id": "health", "title": "Financial Health",
         "value": baseline.get("financial_health_score"), "unit": "/100"},
        {"id": "current_ratio", "title": "Current Ratio",
         "value": liquidity.get("current_ratio"), "unit": "x"},
        {"id": "debt_ebitda", "title": "Debt / EBITDA",
         "value": leverage.get("debt_to_ebitda"), "unit": "x"},
        {"id": "interest_coverage", "title": "Interest Coverage",
         "value": leverage.get("interest_coverage"), "unit": "x"},
        {"id": "net_margin", "title": "Net Profit Margin",
         "value": profitability.get("net_profit_margin_pct"), "unit": "%"},
        {"id": "ccc", "title": "Cash Conversion Cycle",
         "value": wc.get("cash_conversion_cycle_days"), "unit": "days"},
        {"id": "free_cash_flow", "title": "Free Cash Flow",
         "value": cash.get("free_cash_flow"), "unit": "reporting_currency"}
    ]


def risk_analysis(risks):
    risks = sort_risks(risks)
    summary = {"total": len(risks), "critical": 0, "high": 0,
               "medium": 0, "low": 0}

    for risk in risks:
        severity = risk.get("severity", "LOW").lower()
        if severity in summary:
            summary[severity] += 1

    return {"summary": summary, "top_risks": risks[:5]}


def recommendations(recs):
    result = []

    for rec in recs:
        result.append({
            "rank": rec.get("rank"),
            "priority": rec.get("severity"),
            "priority_score": rec.get("priority_score"),
            "category": rec.get("category"),
            "action": rec.get("action"),
            "reason": rec.get("reason"),
            "expected_effect": rec.get("expected_effect"),
            "evidence": rec.get("evidence", {})
        })

    result.sort(
        key=lambda x: (
            -SEVERITY_ORDER.get(x.get("priority", "LOW"), 0),
            -x.get("priority_score", 0)
        )
    )

    for i, rec in enumerate(result, 1):
        rec["rank"] = i

    return result


def stress_summary(stress):
    if not stress:
        return None

    return {
        "scenario": stress.get("scenario", {}),
        "health_score_after_stress": stress.get("financial_health_score"),
        "health_label_after_stress": stress.get("financial_health_label"),
        "fx_impact": stress.get("fx_impact", {}),
        "risks_after_stress": sort_risks(stress.get("risks", [])),
        "recommendations_after_stress": recommendations(
            stress.get("recommendations", [])
        ),
        "comparison": stress.get("comparison", {})
    }


def executive_summary(baseline, risks, stress):
    score = baseline.get("financial_health_score", 0)
    label = baseline.get("financial_health_label", "UNKNOWN")

    if risks:
        top = sort_risks(risks)[0]
        text = (
            f"Financial health is {label.lower()} with a score of {score}/100. "
            f"Highest-priority issue: {top.get('message', '')}"
        )
    else:
        text = (
            f"Financial health is {label.lower()} with a score of {score}/100. "
            "No configured risk conditions were triggered."
        )

    if stress:
        text += (
            f" Under the selected stress scenario, the score changes to "
            f"{stress.get('financial_health_score')}/100 "
            f"({stress.get('financial_health_label', 'UNKNOWN').lower()})."
        )

    return text


def process(raw):
    baseline = raw.get("baseline", {})
    risks = baseline.get("risks", [])
    recs = baseline.get("recommendations", [])
    stress = raw.get("stress_test")

    score = baseline.get("financial_health_score", 0)
    status = health_status(score)

    return {
        "meta": {
            "engine": "FEALS Recommendation Engine",
            "version": "0.1.0",
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "company": raw.get("company", {}),
        "executive_summary": executive_summary(
            baseline, risks, stress
        ),
        "financial_health": {
            "score": score,
            "label": baseline.get("financial_health_label"),
            "status": status["status"],
            "status_color": status["color"]
        },
        "dashboard_cards": dashboard_cards(baseline),
        "risk_analysis": risk_analysis(risks),
        "recommendations": recommendations(recs),
        "stress_test": stress_summary(stress)
    }


if __name__ == "__main__":
    print("FEALS Recommendation Engine started...")

    data = load_json(INPUT_FILE)
    final = process(data)
    save_json(OUTPUT_FILE, final)

    print(f"Completed -> {OUTPUT_FILE}")
    print(
        f"Health: {final['financial_health']['score']}/100 "
        f"({final['financial_health']['label']})"
    )
    print(
        f"Risks: {final['risk_analysis']['summary']['total']} | "
        f"Recommendations: {len(final['recommendations'])}"
    )
