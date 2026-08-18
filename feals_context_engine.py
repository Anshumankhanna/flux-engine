import json
from datetime import datetime, timezone

INPUT_FILE = "output.json"
OUTPUT_FILE = "final_output.json"

SEVERITY = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def sort_risks(items):
    return sorted(items, key=lambda x: SEVERITY.get(x.get("severity", "LOW"), 0), reverse=True)

def health_status(score):
    if score is None: return {"status": "UNKNOWN", "color": "GREY"}
    if score >= 80: return {"status": "HEALTHY", "color": "GREEN"}
    if score >= 60: return {"status": "WATCH", "color": "YELLOW"}
    if score >= 40: return {"status": "STRESSED", "color": "ORANGE"}
    return {"status": "HIGH_RISK", "color": "RED"}

def pct_change(old, new):
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)) or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)

def company_context(raw):
    c = raw.get("company", {})
    return {
        "name": c.get("name"),
        "ticker": c.get("ticker"),
        "industry": c.get("industry"),
        "sector": c.get("sector"),
        "country": c.get("country"),
        "currency": c.get("currency"),
        "fiscal_period": c.get("fiscal_period"),
        "raw_profile": c
    }

def financial_position(raw):
    i = raw.get("income_statement", {})
    b = raw.get("balance_sheet", {})
    cf = raw.get("cash_flow", {})
    wc = raw.get("working_capital", {})
    d = raw.get("debt", {})
    return {
        "income_statement": i,
        "balance_sheet": b,
        "cash_flow": {
            **cf,
            "free_cash_flow": cf.get("operating_cash_flow", 0) - cf.get("capex", 0)
        },
        "working_capital": wc,
        "debt": d
    }

def ratio_context(baseline):
    definitions = {
        "current_ratio": ("Liquidity", "x", "Ability to cover current liabilities with current assets."),
        "quick_ratio": ("Liquidity", "x", "Short-term liquidity excluding inventory."),
        "cash_ratio": ("Liquidity", "x", "Immediate cash coverage of current liabilities."),
        "debt_to_equity": ("Leverage", "x", "Debt relative to shareholders' equity."),
        "debt_to_ebitda": ("Leverage", "x", "Debt relative to EBITDA."),
        "interest_coverage": ("Debt Service", "x", "Ability of operating earnings to cover interest expense."),
        "cash_to_debt": ("Liquidity", "x", "Cash relative to total debt."),
        "net_profit_margin_pct": ("Profitability", "%", "Net income generated from revenue."),
        "ebitda_margin_pct": ("Profitability", "%", "EBITDA generated from revenue."),
        "roa_pct": ("Profitability", "%", "Profit generated relative to total assets."),
        "roe_pct": ("Profitability", "%", "Profit generated relative to equity."),
        "dso_days": ("Working Capital", "days", "Approximate receivable collection period."),
        "dio_days": ("Working Capital", "days", "Approximate inventory holding period."),
        "dpo_days": ("Working Capital", "days", "Approximate supplier payment period."),
        "cash_conversion_cycle_days": ("Working Capital", "days", "Time cash remains tied up in operations."),
        "operating_cash_flow": ("Cash Flow", "currency", "Cash generated from operating activities."),
        "capex": ("Cash Flow", "currency", "Capital expenditure."),
        "free_cash_flow": ("Cash Flow", "currency", "Operating cash flow less capital expenditure.")
    }
    result = []
    for category, metrics in baseline.get("ratios", {}).items():
        for metric, value in metrics.items():
            cat, unit, description = definitions.get(
                metric, (category, "unknown", "FEALS calculated financial metric.")
            )
            result.append({
                "metric": metric,
                "category": cat,
                "value": value,
                "unit": unit,
                "description": description
            })
    return result

def risk_context(baseline):
    risks = sort_risks(baseline.get("risks", []))
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in risks:
        s = r.get("severity", "LOW").lower()
        if s in counts: counts[s] += 1
    return {
        "summary": {"total_risks": len(risks), "by_severity": counts},
        "risks": risks
    }

def recommendation_context(baseline):
    recs = baseline.get("recommendations", [])
    result = []
    for r in recs:
        result.append({
            "rank": r.get("rank"),
            "risk_code": r.get("risk_code"),
            "category": r.get("category"),
            "severity": r.get("severity"),
            "priority_score": r.get("priority_score"),
            "action": r.get("action"),
            "reason": r.get("reason"),
            "expected_effect": r.get("expected_effect"),
            "evidence": r.get("evidence", {})
        })
    result.sort(key=lambda x: (-SEVERITY.get(x.get("severity", "LOW"), 0),
                               -x.get("priority_score", 0)))
    for n, r in enumerate(result, 1): r["rank"] = n
    return result

def stress_context(raw, baseline):
    s = raw.get("stress_test")
    if not s:
        return {"available": False, "message": "No stress scenario supplied."}
    changes = []
    for category, metrics in s.get("comparison", {}).items():
        if not isinstance(metrics, dict): continue
        for metric, v in metrics.items():
            if not isinstance(v, dict): continue
            changes.append({
                "category": category,
                "metric": metric,
                "baseline": v.get("baseline"),
                "stressed": v.get("stressed"),
                "absolute_change": v.get("delta"),
                "percentage_change": pct_change(v.get("baseline"), v.get("stressed"))
            })
    return {
        "available": True,
        "scenario_inputs": s.get("scenario", {}),
        "baseline_health": {
            "score": baseline.get("financial_health_score"),
            "label": baseline.get("financial_health_label")
        },
        "stressed_health": {
            "score": s.get("financial_health_score"),
            "label": s.get("financial_health_label")
        },
        "health_score_change": (
            s.get("financial_health_score", 0) -
            baseline.get("financial_health_score", 0)
        ),
        "fx_impact": s.get("fx_impact", {}),
        "metric_changes": changes,
        "risks_after_stress": sort_risks(s.get("risks", [])),
        "recommendations_after_stress": s.get("recommendations", [])
    }

def data_quality(raw):
    required = [
        ("income_statement","revenue"), ("income_statement","ebit"),
        ("income_statement","ebitda"), ("income_statement","net_income"),
        ("income_statement","interest_expense"), ("balance_sheet","cash"),
        ("balance_sheet","current_assets"), ("balance_sheet","current_liabilities"),
        ("balance_sheet","total_assets"), ("balance_sheet","total_debt"),
        ("balance_sheet","equity"), ("balance_sheet","inventory"),
        ("balance_sheet","accounts_receivable"), ("balance_sheet","accounts_payable"),
        ("cash_flow","operating_cash_flow"), ("cash_flow","capex")
    ]
    missing = []
    for path in required:
        cur = raw
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                missing.append(".".join(path))
                break
            cur = cur[key]
    completeness = round((len(required) - len(missing)) / len(required) * 100, 2)
    return {
        "completeness_percentage": completeness,
        "missing_fields": missing,
        "analysis_quality": "HIGH" if completeness >= 95 else "MEDIUM" if completeness >= 80 else "LOW",
        "warnings": ["Some financial fields are missing."] if missing else []
    }

def executive_summary(baseline, risks, recs, stress):
    score = baseline.get("financial_health_score")
    label = baseline.get("financial_health_label", "UNKNOWN")
    text = f"Financial health is {label.lower()} with a score of {score}/100."
    if risks["risks"]:
        top = risks["risks"][0]
        text += f" Highest-priority issue: {top.get('message', '')}"
    else:
        text += " No configured risk conditions were triggered."
    if stress.get("available"):
        text += (
            f" Under the selected stress scenario, the score changes to "
            f"{stress['stressed_health']['score']}/100 "
            f"({stress['stressed_health']['label'].lower()})."
        )
    return text

def build_bedrock_context(company, position, ratios, risks, recs, stress, quality):
    return {
        "instruction": (
            "Analyze the supplied financial information as a decision-support "
            "system. Use only supplied data. Do not invent figures or override "
            "FEALS calculations. Explain key risks, stress-test impact, and "
            "recommended management actions. State assumptions and limitations."
        ),
        "company": company,
        "financial_position": position,
        "calculated_ratios": ratios,
        "risk_assessment": risks,
        "recommendations": recs,
        "stress_test": stress,
        "data_quality": quality,
        "requested_output": [
            "Executive financial summary",
            "Top financial risks",
            "Drivers behind each risk",
            "Stress-test impact",
            "Prioritized management actions",
            "Expected effect of each action",
            "Assumptions and data limitations"
        ]
    }

def process(raw):
    baseline = raw.get("baseline", {})
    company = company_context(raw)
    position = financial_position(raw)
    ratios = ratio_context(baseline)
    risks = risk_context(baseline)
    recs = recommendation_context(baseline)
    stress = stress_context(raw, baseline)
    quality = data_quality(raw)
    score = baseline.get("financial_health_score")
    status = health_status(score)

    return {
        "meta": {
            "engine": "FEALS Financial Context & Intelligence Engine",
            "version": "0.2.0",
            "generated_at": datetime.now(timezone.utc).isoformat()
        },
        "company": company,
        "executive_summary": executive_summary(baseline, risks, recs, stress),
        "financial_health": {
            "score": score,
            "label": baseline.get("financial_health_label"),
            "status": status["status"],
            "status_color": status["color"]
        },
        "financial_position": position,
        "financial_ratios": ratios,
        "risk_analysis": risks,
        "recommendations": {
            "count": len(recs),
            "items": recs
        },
        "stress_test": stress,
        "data_quality": quality,
        "bedrock_context": build_bedrock_context(
            company, position, ratios, risks, recs, stress, quality
        )
    }

if __name__ == "__main__":
    print("FEALS Financial Context Engine started...")
    try:
        raw = load_json(INPUT_FILE)
        result = process(raw)
        save_json(OUTPUT_FILE, result)
        print(f"Completed -> {OUTPUT_FILE}")
        print(f"Health: {result['financial_health']['score']}/100 "
              f"({result['financial_health']['label']})")
        print(f"Risks: {result['risk_analysis']['summary']['total_risks']}")
        print(f"Recommendations: {result['recommendations']['count']}")
        print(f"Ratios: {len(result['financial_ratios'])}")
        print("Bedrock context: READY")
    except FileNotFoundError:
        print(f"ERROR: {INPUT_FILE} not found.")
    except json.JSONDecodeError:
        print(f"ERROR: {INPUT_FILE} is not valid JSON.")
    except Exception as e:
        print(f"ERROR: {e}")
