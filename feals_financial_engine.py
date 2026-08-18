"""
FEALS Financial Data Engine
----------------------------
AWS Lambda function that:

1. Reads a financial-input JSON file from Amazon S3.
2. Calculates financial ratios.
3. Assesses financial risk using configurable, illustrative thresholds.
4. Runs an optional stress scenario.
5. Generates ranked management-action recommendations.
6. Writes the complete analysis JSON back to S3.

Expected Lambda handler:
    lambda_function.lambda_handler

The function can be invoked in two ways:
A) S3 ObjectCreated event
B) Direct Lambda invocation:
   {
       "bucket": "my-feals-bucket",
       "key": "input/company_001.json"
   }

For S3-triggered execution, configure the Lambda trigger for the input/
prefix and make sure the output/ prefix does NOT trigger the same Lambda.
"""

import json
import math
import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote_plus

import boto3

s3 = boto3.client("s3")

OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "output/")
DEFAULT_DAYS = 365


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def safe_number(value, default=0.0):
    """Convert a JSON number/string/Decimal to float safely."""
    if value is None or value == "":
        return float(default)

    try:
        return float(value)
    except (TypeError, ValueError, InvalidOperation):
        return float(default)


def safe_divide(numerator, denominator):
    """Return None instead of crashing on division by zero."""
    numerator = safe_number(numerator)
    denominator = safe_number(denominator)

    if denominator == 0:
        return None

    return numerator / denominator


def pct(value):
    """Convert ratio to percentage."""
    return None if value is None else value * 100


def round_value(value, digits=4):
    if value is None:
        return None
    return round(value, digits)


def get_nested(data, *keys, default=0.0):
    """Safely retrieve nested financial data."""
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)

    return default if current is None else current


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def normalize_input(raw):
    """
    Normalize the financial JSON into a predictable internal structure.

    The engine expects the following top-level sections:
      company
      income_statement
      balance_sheet
      cash_flow
      working_capital
      debt
      market (optional)
      scenario (optional)
    """

    income = raw.get("income_statement", {})
    balance = raw.get("balance_sheet", {})
    cash_flow = raw.get("cash_flow", {})
    wc = raw.get("working_capital", {})
    debt = raw.get("debt", {})
    market = raw.get("market", {})

    # Accept common alternative field names as fallbacks.
    normalized = {
        "company": raw.get("company", {}),

        "income_statement": {
            "revenue": safe_number(
                income.get("revenue", raw.get("revenue", 0))
            ),
            "ebit": safe_number(
                income.get("ebit", raw.get("ebit", 0))
            ),
            "ebitda": safe_number(
                income.get("ebitda", raw.get("ebitda", 0))
            ),
            "net_income": safe_number(
                income.get("net_income", raw.get("net_income", 0))
            ),
            "interest_expense": safe_number(
                income.get("interest_expense", raw.get("interest_expense", 0))
            ),
            "cogs": safe_number(
                income.get("cogs", raw.get("cogs", 0))
            ),
            "operating_expenses": safe_number(
                income.get("operating_expenses", raw.get("operating_expenses", 0))
            ),
        },

        "balance_sheet": {
            "cash": safe_number(
                balance.get("cash", raw.get("cash", 0))
            ),
            "current_assets": safe_number(
                balance.get("current_assets", raw.get("current_assets", 0))
            ),
            "current_liabilities": safe_number(
                balance.get("current_liabilities", raw.get("current_liabilities", 0))
            ),
            "total_assets": safe_number(
                balance.get("total_assets", raw.get("total_assets", 0))
            ),
            "total_debt": safe_number(
                balance.get(
                    "total_debt",
                    balance.get("debt", raw.get("debt", 0))
                )
            ),
            "equity": safe_number(
                balance.get("equity", raw.get("equity", 0))
            ),
            "inventory": safe_number(
                balance.get("inventory", raw.get("inventory", 0))
            ),
            "accounts_receivable": safe_number(
                balance.get(
                    "accounts_receivable",
                    balance.get("receivables", raw.get("receivables", 0))
                )
            ),
            "accounts_payable": safe_number(
                balance.get(
                    "accounts_payable",
                    balance.get("payables", raw.get("payables", 0))
                )
            ),
        },

        "cash_flow": {
            "operating_cash_flow": safe_number(
                cash_flow.get(
                    "operating_cash_flow",
                    raw.get("operating_cash_flow", 0)
                )
            ),
            "capex": safe_number(
                cash_flow.get("capex", raw.get("capex", 0))
            ),
        },

        "working_capital": {
            "accounts_receivable": safe_number(
                wc.get(
                    "accounts_receivable",
                    balance.get(
                        "accounts_receivable",
                        balance.get("receivables", raw.get("receivables", 0))
                    )
                )
            ),
            "inventory": safe_number(
                wc.get(
                    "inventory",
                    balance.get("inventory", raw.get("inventory", 0))
                )
            ),
            "accounts_payable": safe_number(
                wc.get(
                    "accounts_payable",
                    balance.get(
                        "accounts_payable",
                        balance.get("payables", raw.get("payables", 0))
                    )
                )
            ),
        },

        "debt": {
            "total_debt": safe_number(
                debt.get(
                    "total_debt",
                    balance.get("total_debt", 0)
                )
            ),
            "interest_rate": safe_number(debt.get("interest_rate", 0)),
        },

        "market": {
            "fx_exposure": market.get("fx_exposure", {}),
            "fx_rates": market.get("fx_rates", {}),
        },

        "scenario": raw.get("scenario", {}),
    }

    return normalized


# ---------------------------------------------------------------------------
# Ratio engine
# ---------------------------------------------------------------------------

def calculate_ratios(data):
    income = data["income_statement"]
    balance = data["balance_sheet"]
    cash_flow = data["cash_flow"]
    wc = data["working_capital"]

    revenue = income["revenue"]
    ebit = income["ebit"]
    ebitda = income["ebitda"]
    net_income = income["net_income"]
    interest = income["interest_expense"]
    cogs = income["cogs"]

    cash = balance["cash"]
    current_assets = balance["current_assets"]
    current_liabilities = balance["current_liabilities"]
    total_assets = balance["total_assets"]
    debt = balance["total_debt"]
    equity = balance["equity"]
    inventory = balance["inventory"]

    receivables = wc["accounts_receivable"]
    payables = wc["accounts_payable"]

    current_ratio = safe_divide(current_assets, current_liabilities)

    quick_assets = current_assets - inventory
    quick_ratio = safe_divide(quick_assets, current_liabilities)

    cash_ratio = safe_divide(cash, current_liabilities)

    debt_equity = safe_divide(debt, equity)
    debt_ebitda = safe_divide(debt, ebitda)
    interest_coverage = safe_divide(ebit, interest)

    net_margin = safe_divide(net_income, revenue)
    ebitda_margin = safe_divide(ebitda, revenue)
    roa = safe_divide(net_income, total_assets)
    roe = safe_divide(net_income, equity)

    dso = safe_divide(receivables, revenue)
    dso = None if dso is None else dso * DEFAULT_DAYS

    dio = safe_divide(inventory, cogs)
    dio = None if dio is None else dio * DEFAULT_DAYS

    dpo = safe_divide(payables, cogs)
    dpo = None if dpo is None else dpo * DEFAULT_DAYS

    ccc = None
    if dso is not None and dio is not None and dpo is not None:
        ccc = dso + dio - dpo

    operating_cash_flow = cash_flow["operating_cash_flow"]
    capex = cash_flow["capex"]
    free_cash_flow = operating_cash_flow - capex

    cash_to_debt = safe_divide(cash, debt)

    return {
        "liquidity": {
            "current_ratio": round_value(current_ratio),
            "quick_ratio": round_value(quick_ratio),
            "cash_ratio": round_value(cash_ratio),
        },

        "leverage": {
            "debt_to_equity": round_value(debt_equity),
            "debt_to_ebitda": round_value(debt_ebitda),
            "interest_coverage": round_value(interest_coverage),
            "cash_to_debt": round_value(cash_to_debt),
        },

        "profitability": {
            "net_profit_margin_pct": round_value(pct(net_margin), 2),
            "ebitda_margin_pct": round_value(pct(ebitda_margin), 2),
            "roa_pct": round_value(pct(roa), 2),
            "roe_pct": round_value(pct(roe), 2),
        },

        "working_capital": {
            "dso_days": round_value(dso, 2),
            "dio_days": round_value(dio, 2),
            "dpo_days": round_value(dpo, 2),
            "cash_conversion_cycle_days": round_value(ccc, 2),
        },

        "cash_flow": {
            "operating_cash_flow": round_value(operating_cash_flow, 2),
            "capex": round_value(capex, 2),
            "free_cash_flow": round_value(free_cash_flow, 2),
        },
    }


# ---------------------------------------------------------------------------
# Risk rules
# IMPORTANT:
# These thresholds are illustrative starting rules, not universal finance law.
# They should eventually be replaced/adjusted by industry-specific benchmarks.
# ---------------------------------------------------------------------------

def evaluate_risks(ratios, data):
    risks = []

    def add_risk(code, category, metric, value, severity, message):
        risks.append({
            "code": code,
            "category": category,
            "metric": metric,
            "value": value,
            "severity": severity,
            "message": message,
        })

    liquidity = ratios["liquidity"]
    leverage = ratios["leverage"]
    profitability = ratios["profitability"]
    wc = ratios["working_capital"]

    current_ratio = liquidity["current_ratio"]
    quick_ratio = liquidity["quick_ratio"]
    debt_ebitda = leverage["debt_to_ebitda"]
    interest_coverage = leverage["interest_coverage"]
    net_margin = profitability["net_profit_margin_pct"]
    dso = wc["dso_days"]
    ccc = wc["cash_conversion_cycle_days"]

    if current_ratio is not None:
        if current_ratio < 1.0:
            add_risk(
                "LOW_CURRENT_RATIO",
                "LIQUIDITY",
                "current_ratio",
                current_ratio,
                "CRITICAL",
                "Current liabilities exceed current assets."
            )
        elif current_ratio < 1.5:
            add_risk(
                "WATCH_CURRENT_RATIO",
                "LIQUIDITY",
                "current_ratio",
                current_ratio,
                "MEDIUM",
                "Short-term liquidity should be monitored."
            )

    if quick_ratio is not None and quick_ratio < 0.8:
        add_risk(
            "LOW_QUICK_RATIO",
            "LIQUIDITY",
            "quick_ratio",
            quick_ratio,
            "HIGH",
            "Liquid assets may be insufficient to cover short-term liabilities."
        )

    if debt_ebitda is not None:
        if debt_ebitda > 4.0:
            add_risk(
                "HIGH_DEBT_EBITDA",
                "LEVERAGE",
                "debt_to_ebitda",
                debt_ebitda,
                "HIGH",
                "Debt is high relative to EBITDA."
            )
        elif debt_ebitda > 3.0:
            add_risk(
                "ELEVATED_DEBT_EBITDA",
                "LEVERAGE",
                "debt_to_ebitda",
                debt_ebitda,
                "MEDIUM",
                "Leverage should be monitored."
            )

    if interest_coverage is not None:
        if interest_coverage < 2.0:
            add_risk(
                "LOW_INTEREST_COVERAGE",
                "DEBT_SERVICE",
                "interest_coverage",
                interest_coverage,
                "CRITICAL",
                "Operating earnings provide limited coverage of interest expense."
            )
        elif interest_coverage < 3.0:
            add_risk(
                "WATCH_INTEREST_COVERAGE",
                "DEBT_SERVICE",
                "interest_coverage",
                interest_coverage,
                "MEDIUM",
                "Debt-servicing capacity should be monitored."
            )

    if dso is not None:
        if dso > 90:
            add_risk(
                "HIGH_DSO",
                "RECEIVABLES",
                "dso_days",
                dso,
                "HIGH",
                "Receivables are taking substantially longer to convert into cash."
            )
        elif dso > 60:
            add_risk(
                "ELEVATED_DSO",
                "RECEIVABLES",
                "dso_days",
                dso,
                "MEDIUM",
                "Collection efficiency should be reviewed."
            )

    if ccc is not None:
        if ccc > 120:
            add_risk(
                "HIGH_CCC",
                "WORKING_CAPITAL",
                "cash_conversion_cycle_days",
                ccc,
                "HIGH",
                "A large amount of cash may be tied up in the operating cycle."
            )
        elif ccc > 90:
            add_risk(
                "ELEVATED_CCC",
                "WORKING_CAPITAL",
                "cash_conversion_cycle_days",
                ccc,
                "MEDIUM",
                "Working-capital efficiency should be reviewed."
            )

    if net_margin is not None and net_margin < 0:
        add_risk(
            "NEGATIVE_NET_MARGIN",
            "PROFITABILITY",
            "net_profit_margin_pct",
            net_margin,
            "CRITICAL",
            "The company is currently reporting a negative net margin."
        )

    return risks


# ---------------------------------------------------------------------------
# Stress engine
# ---------------------------------------------------------------------------

def apply_stress_scenario(data, scenario):
    """
    Create a stressed copy without mutating the original financial state.

    Supported scenario fields:
      revenue_change_pct
      operating_cost_change_pct
      interest_rate_change_pct
      fx_change_pct
      receivable_delay_days
      inventory_change_pct
      capex_change_pct

    Percentages are supplied as decimal values:
      -0.10 = -10%
       0.05 = +5%
    """

    stressed = json.loads(json.dumps(data))

    revenue_change = safe_number(scenario.get("revenue_change_pct", 0))
    cost_change = safe_number(scenario.get("operating_cost_change_pct", 0))
    interest_rate_change = safe_number(
        scenario.get("interest_rate_change_pct", 0)
    )
    receivable_delay = safe_number(
        scenario.get("receivable_delay_days", 0)
    )
    inventory_change = safe_number(
        scenario.get("inventory_change_pct", 0)
    )
    capex_change = safe_number(
        scenario.get("capex_change_pct", 0)
    )

    income = stressed["income_statement"]
    balance = stressed["balance_sheet"]
    cash_flow = stressed["cash_flow"]
    wc = stressed["working_capital"]

    original_revenue = income["revenue"]
    original_opex = income["operating_expenses"]

    # Revenue shock.
    new_revenue = original_revenue * (1 + revenue_change)

    # Operating cost shock.
    new_opex = original_opex * (1 + cost_change)

    # If operating expenses are not supplied, do not manufacture a cost base.
    # Revenue shock still affects EBIT/EBITDA proportionally in that case.
    if original_opex > 0:
        opex_delta = new_opex - original_opex
    else:
        opex_delta = 0

    revenue_delta = new_revenue - original_revenue

    income["revenue"] = new_revenue
    income["operating_expenses"] = new_opex

    income["ebitda"] = max(
        0,
        income["ebitda"] + revenue_delta - opex_delta
    )

    income["ebit"] = max(
        0,
        income["ebit"] + revenue_delta - opex_delta
    )

    # Keep the relationship between EBIT and net income approximately intact
    # for this simplified stress model.
    income["net_income"] = (
        income["ebit"] - income["interest_expense"]
    )

    # Interest-rate shock.
    debt = stressed["debt"]["total_debt"]
    rate_change = interest_rate_change

    additional_interest = debt * rate_change
    income["interest_expense"] += additional_interest

    income["net_income"] = (
        income["ebit"] - income["interest_expense"]
    )

    # Receivable delay: approximate additional receivables based on
    # revenue/day. This is deliberately transparent rather than pretending
    # to be a full working-capital forecasting model.
    daily_revenue = safe_divide(new_revenue, DEFAULT_DAYS)
    if daily_revenue is not None:
        additional_receivables = daily_revenue * receivable_delay
        wc["accounts_receivable"] += additional_receivables
        balance["accounts_receivable"] = wc["accounts_receivable"]

        # The same delay consumes cash in this simplified model.
        balance["cash"] -= additional_receivables

    # Inventory shock.
    inventory = wc["inventory"]
    new_inventory = inventory * (1 + inventory_change)
    wc["inventory"] = new_inventory
    balance["inventory"] = new_inventory

    # CAPEX shock.
    cash_flow["capex"] *= (1 + capex_change)

    # Recalculate FCF.
    cash_flow["free_cash_flow"] = (
        cash_flow["operating_cash_flow"] - cash_flow["capex"]
    )

    # FX impact on explicitly provided foreign-currency liabilities.
    fx_impact = calculate_fx_stress_impact(
        stressed.get("market", {}),
        safe_number(scenario.get("fx_change_pct", 0))
    )

    balance["cash"] += fx_impact["cash_effect"]

    return stressed, fx_impact


def calculate_fx_stress_impact(market, fx_change_pct):
    """
    Simplified FX stress calculation.

    Expected input example:
    "fx_exposure": {
        "USD": {
            "liability": 2000000,
            "rate_to_reporting_currency": 83.0
        }
    }

    Positive fx_change_pct means the foreign currency becomes more
    expensive in reporting currency.

    This function only estimates the impact of explicitly provided
    foreign-currency liabilities. It does not model hedge accounting.
    """

    exposure = market.get("fx_exposure", {})
    total_impact = 0.0
    details = []

    for currency, item in exposure.items():
        if not isinstance(item, dict):
            continue

        liability = safe_number(item.get("liability", 0))
        current_rate = safe_number(
            item.get("rate_to_reporting_currency", 0)
        )

        if liability == 0 or current_rate == 0:
            continue

        old_value = liability * current_rate
        new_value = liability * current_rate * (1 + fx_change_pct)
        impact = new_value - old_value

        total_impact += impact

        details.append({
            "currency": currency,
            "liability": liability,
            "current_rate": current_rate,
            "stressed_rate": round_value(
                current_rate * (1 + fx_change_pct), 6
            ),
            "reporting_currency_impact": round_value(impact, 2),
        })

    # A foreign-currency liability becoming more expensive is a negative
    # economic effect, so reduce stressed cash by the incremental liability.
    return {
        "cash_effect": -total_impact,
        "total_reporting_currency_impact": round_value(total_impact, 2),
        "details": details,
    }


# ---------------------------------------------------------------------------
# Recommendation engine
# ---------------------------------------------------------------------------

SEVERITY_SCORE = {
    "CRITICAL": 100,
    "HIGH": 75,
    "MEDIUM": 50,
    "LOW": 25,
}


RECOMMENDATIONS = {
    "LOW_CURRENT_RATIO": {
        "priority": 100,
        "actions": [
            {
                "action": "Accelerate receivables collection",
                "reason": "Increase near-term cash inflows.",
                "expected_effect": "Improve short-term liquidity."
            },
            {
                "action": "Review short-term liabilities and payment timing",
                "reason": "Reduce immediate liquidity pressure.",
                "expected_effect": "Improve current-liability coverage."
            },
            {
                "action": "Evaluate available short-term liquidity facilities",
                "reason": "Provide additional liquidity if a funding gap is projected.",
                "expected_effect": "Increase liquidity buffer."
            },
        ],
    },

    "WATCH_CURRENT_RATIO": {
        "priority": 60,
        "actions": [
            {
                "action": "Monitor near-term cash commitments",
                "reason": "Liquidity is below a stronger comfort zone.",
                "expected_effect": "Reduce probability of future liquidity stress."
            },
        ],
    },

    "LOW_QUICK_RATIO": {
        "priority": 80,
        "actions": [
            {
                "action": "Review inventory dependence for short-term liquidity",
                "reason": "A significant portion of current assets may be tied up in inventory.",
                "expected_effect": "Improve liquid-asset coverage."
            },
        ],
    },

    "HIGH_DEBT_EBITDA": {
        "priority": 85,
        "actions": [
            {
                "action": "Evaluate debt reduction or refinancing options",
                "reason": "Debt is high relative to operating earnings.",
                "expected_effect": "Reduce leverage and debt-service pressure."
            },
            {
                "action": "Review non-essential capital expenditure",
                "reason": "Preserve cash for debt servicing and core operations.",
                "expected_effect": "Protect liquidity."
            },
        ],
    },

    "ELEVATED_DEBT_EBITDA": {
        "priority": 60,
        "actions": [
            {
                "action": "Monitor leverage and future borrowing requirements",
                "reason": "Leverage is elevated.",
                "expected_effect": "Prevent further deterioration in debt capacity."
            },
        ],
    },

    "LOW_INTEREST_COVERAGE": {
        "priority": 95,
        "actions": [
            {
                "action": "Evaluate refinancing or debt restructuring",
                "reason": "Operating earnings provide limited interest coverage.",
                "expected_effect": "Reduce debt-service pressure."
            },
            {
                "action": "Review opportunities to reduce financing costs",
                "reason": "Lower financing costs can improve coverage.",
                "expected_effect": "Increase interest coverage."
            },
        ],
    },

    "WATCH_INTEREST_COVERAGE": {
        "priority": 60,
        "actions": [
            {
                "action": "Monitor interest expense and refinancing requirements",
                "reason": "Debt-servicing capacity is becoming less resilient.",
                "expected_effect": "Reduce future financing risk."
            },
        ],
    },

    "HIGH_DSO": {
        "priority": 90,
        "actions": [
            {
                "action": "Accelerate receivables collection",
                "reason": "Receivables are converting to cash slowly.",
                "expected_effect": "Improve operating cash flow."
            },
            {
                "action": "Review customer credit terms",
                "reason": "Long collection periods can increase working-capital requirements.",
                "expected_effect": "Reduce DSO and cash tied up in receivables."
            },
        ],
    },

    "ELEVATED_DSO": {
        "priority": 60,
        "actions": [
            {
                "action": "Review collection performance by customer",
                "reason": "Collection efficiency is weakening.",
                "expected_effect": "Improve cash conversion."
            },
        ],
    },

    "HIGH_CCC": {
        "priority": 85,
        "actions": [
            {
                "action": "Optimize receivables, inventory and supplier payment cycles",
                "reason": "The operating cycle is tying up significant cash.",
                "expected_effect": "Release working capital."
            },
        ],
    },

    "ELEVATED_CCC": {
        "priority": 55,
        "actions": [
            {
                "action": "Review working-capital efficiency",
                "reason": "Cash conversion is slower than the illustrative threshold.",
                "expected_effect": "Reduce cash tied up in operations."
            },
        ],
    },

    "NEGATIVE_NET_MARGIN": {
        "priority": 95,
        "actions": [
            {
                "action": "Review major operating-cost drivers and pricing",
                "reason": "The company is currently loss-making on a net-income basis.",
                "expected_effect": "Improve profitability."
            },
        ],
    },
}


def generate_recommendations(risks, scenario_risks=None):
    all_risks = list(risks)

    if scenario_risks:
        for risk in scenario_risks:
            scenario_copy = dict(risk)
            scenario_copy["scenario_triggered"] = True
            all_risks.append(scenario_copy)

    recommendations = []

    for risk in all_risks:
        code = risk["code"]
        rule = RECOMMENDATIONS.get(code)

        if not rule:
            continue

        for action in rule["actions"]:
            recommendations.append({
                "risk_code": code,
                "category": risk["category"],
                "severity": risk["severity"],
                "priority_score": rule["priority"],
                "action": action["action"],
                "reason": action["reason"],
                "expected_effect": action["expected_effect"],
                "evidence": {
                    "metric": risk["metric"],
                    "value": risk["value"],
                    "message": risk["message"],
                },
            })

    # Deduplicate actions while retaining the strongest priority.
    deduped = {}

    for rec in recommendations:
        key = rec["action"]

        if (
            key not in deduped
            or rec["priority_score"] > deduped[key]["priority_score"]
        ):
            deduped[key] = rec

    recommendations = list(deduped.values())

    recommendations.sort(
        key=lambda x: (
            -x["priority_score"],
            -SEVERITY_SCORE.get(x["severity"], 0),
        )
    )

    for index, rec in enumerate(recommendations, start=1):
        rec["rank"] = index

    return recommendations


# ---------------------------------------------------------------------------
# Financial health score
# ---------------------------------------------------------------------------

def calculate_health_score(risks):
    """
    Illustrative risk score.

    Starts at 100 and deducts points based on the highest-severity
    risk categories. This is NOT a credit score and should be clearly
    labelled as an FEALS internal health indicator.
    """

    deductions = {
        "CRITICAL": 25,
        "HIGH": 15,
        "MEDIUM": 8,
        "LOW": 3,
    }

    score = 100
    categories_seen = set()

    for risk in risks:
        category = risk["category"]

        # Avoid disproportionately penalizing many rules from the
        # same category.
        if category in categories_seen:
            continue

        categories_seen.add(category)
        score -= deductions.get(risk["severity"], 0)

    return max(0, min(100, score))


def health_label(score):
    if score >= 80:
        return "HEALTHY"
    if score >= 60:
        return "WATCH"
    if score >= 40:
        return "STRESSED"
    return "HIGH_RISK"


# ---------------------------------------------------------------------------
# Full analysis
# ---------------------------------------------------------------------------

def analyze_financial_state(raw_data):
    data = normalize_input(raw_data)

    baseline_ratios = calculate_ratios(data)
    baseline_risks = evaluate_risks(baseline_ratios, data)
    baseline_score = calculate_health_score(baseline_risks)

    result = {
        "engine": {
            "name": "FEALS Financial Data Engine",
            "version": "0.1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "disclaimer": (
                "Risk thresholds and recommendations are illustrative "
                "decision-support rules and require validation by qualified "
                "finance professionals before production use."
            ),
        },

        "company": data["company"],

        "baseline": {
            "ratios": baseline_ratios,
            "risks": baseline_risks,
            "financial_health_score": baseline_score,
            "financial_health_label": health_label(baseline_score),
        },

        "stress_test": None,

        "recommendations": generate_recommendations(baseline_risks),
    }

    scenario = data.get("scenario") or {}

    if scenario:
        stressed_data, fx_impact = apply_stress_scenario(
            data,
            scenario
        )

        stressed_ratios = calculate_ratios(stressed_data)
        stressed_risks = evaluate_risks(
            stressed_ratios,
            stressed_data
        )

        stressed_score = calculate_health_score(stressed_risks)

        # Compare the baseline and stressed metrics.
        comparison = build_comparison(
            baseline_ratios,
            stressed_ratios
        )

        result["stress_test"] = {
            "scenario": scenario,
            "fx_impact": fx_impact,
            "ratios": stressed_ratios,
            "risks": stressed_risks,
            "financial_health_score": stressed_score,
            "financial_health_label": health_label(stressed_score),
            "comparison": comparison,
        }

        result["stress_test"]["recommendations"] = (
            generate_recommendations(
                baseline_risks,
                scenario_risks=stressed_risks
            )
        )

    return result


def build_comparison(baseline, stressed):
    comparison = {}

    for category, metrics in baseline.items():
        comparison[category] = {}

        for metric, baseline_value in metrics.items():
            stressed_value = (
                stressed.get(category, {}).get(metric)
            )

            delta = None
            if (
                isinstance(baseline_value, (int, float))
                and isinstance(stressed_value, (int, float))
            ):
                delta = stressed_value - baseline_value

            comparison[category][metric] = {
                "baseline": baseline_value,
                "stressed": stressed_value,
                "delta": round_value(delta, 4),
            }

    return comparison


# ---------------------------------------------------------------------------
# S3 I/O
# ---------------------------------------------------------------------------

def read_json_from_s3(bucket, key):
    response = s3.get_object(
        Bucket=bucket,
        Key=key
    )

    body = response["Body"].read().decode("utf-8")
    return json.loads(body)


def write_json_to_s3(bucket, key, data):
    payload = json.dumps(
        data,
        indent=2,
        ensure_ascii=False
    )

    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=payload.encode("utf-8"),
        ContentType="application/json",
    )


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def get_s3_location_from_event(event):
    """
    Supports:
    1. S3 ObjectCreated event
    2. Direct Lambda invocation with bucket/key
    """

    # Direct invocation.
    if isinstance(event, dict) and event.get("bucket") and event.get("key"):
        return event["bucket"], event["key"]

    # Standard S3 notification.
    records = event.get("Records", [])

    if records:
        record = records[0]

        if record.get("eventSource") == "aws:s3":
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])

            return bucket, key

    raise ValueError(
        "Could not determine S3 bucket/key from Lambda event."
    )


def build_output_key(input_key):
    filename = input_key.split("/")[-1]

    if filename.lower().endswith(".json"):
        filename = filename[:-5]

    timestamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    return f"{OUTPUT_PREFIX}{filename}_analysis_{timestamp}.json"


def lambda_handler(event, context):
    try:
        input_bucket, input_key = get_s3_location_from_event(event)

        print(
            f"FEALS engine started. "
            f"Input: s3://{input_bucket}/{input_key}"
        )

        raw_data = read_json_from_s3(
            input_bucket,
            input_key
        )

        analysis = analyze_financial_state(raw_data)

        output_key = build_output_key(input_key)

        write_json_to_s3(
            input_bucket,
            output_key,
            analysis
        )

        print(
            f"FEALS analysis written to "
            f"s3://{input_bucket}/{output_key}"
        )

        return {
            "statusCode": 200,
            "status": "SUCCESS",
            "input": {
                "bucket": input_bucket,
                "key": input_key,
            },
            "output": {
                "bucket": input_bucket,
                "key": output_key,
            },
            "financial_health_score": (
                analysis["baseline"]["financial_health_score"]
            ),
            "financial_health_label": (
                analysis["baseline"]["financial_health_label"]
            ),
        }

    except Exception as exc:
        print(f"FEALS engine failed: {str(exc)}")

        # Lambda/API Gateway can consume this structured response.
        return {
            "statusCode": 500,
            "status": "ERROR",
            "error": str(exc),
        }
    
if __name__ == "__main__":
    with open("feals_sample_input.json", "r") as f:
        data = json.load(f)

    result = analyze_financial_state(data)

    with open("output.json", "w") as f:
        json.dump(result, f, indent=2)

    print("Analysis completed → output.json")