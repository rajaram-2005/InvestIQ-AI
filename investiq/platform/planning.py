"""Deterministic planning arithmetic; decimal values are serialized as strings."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

CENT = Decimal('0.01')

def money(value: Decimal) -> str:
    with localcontext() as ctx:
        ctx.prec = 100
        return str(value.quantize(CENT, rounding=ROUND_HALF_UP))

def savings(monthly: Decimal, months: int) -> dict:
    return {'total': money(monthly * months), 'formula': 'monthly contribution × number of months',
            'assumptions': 'No interest, investment returns, fees, taxes or inflation; future contributions are not guaranteed.'}

def compound(principal: Decimal, annual_rate_pct: Decimal, months: int) -> dict:
    # Nominal annual rate, monthly compounding; rounded once, at output.
    with localcontext() as ctx:
        ctx.prec = 50
        total = principal * (Decimal(1) + annual_rate_pct / Decimal(1200)) ** months
    return {'total': money(total), 'interest': money(total-principal),
            'formula': 'principal × (1 + annual rate / 12) ^ months',
            'assumptions': 'Nominal annual rate compounded monthly; no tax, fees, rate changes or guarantee.'}

def sip(monthly: Decimal, annual_rate_pct: Decimal, months: int) -> dict:
    # End-of-month payments; explicit zero-rate branch avoids division by zero.
    with localcontext() as ctx:
        ctx.prec = 50
        rate = annual_rate_pct / Decimal(1200)
        total = monthly * months if not rate else monthly * (((1+rate) ** months - 1) / rate)
    invested = monthly * months
    return {'total': money(total), 'contributions': money(invested), 'gain_or_loss': money(total-invested),
            'formula': 'monthly contribution × (((1 + monthly rate) ^ months − 1) / monthly rate), end-of-month',
            'assumptions': 'Constant assumed nominal annual rate, monthly compounding and end-of-month contributions; no tax or fees. Returns are uncertain and not guaranteed.'}

def mission_progress(plan: dict, contributions: list[dict], today: date) -> dict:
    actual = sum((c['amount'] for c in contributions), Decimal(0))
    monthly = plan['monthly_contribution']
    # Count complete calendar months only; no assumption that the current month has been missed.
    start = plan['start_date']
    current_month_index = (today.year-start.year)*12 + today.month-start.month
    completed_months = max(0, min(plan['months'], current_month_index))
    by_month = {}
    for c in contributions:
        key = (c['contribution_date'].year, c['contribution_date'].month)
        by_month[key] = by_month.get(key, Decimal(0)) + c['amount']
    missed = []
    for i in range(completed_months):
        n = start.year*12 + start.month-1+i
        year, month = divmod(n, 12); month += 1
        paid = by_month.get((year, month), Decimal(0))
        if paid < monthly:
            missed.append({'month': f'{year:04d}-{month:02d}', 'contributed': money(paid), 'planned': money(monthly)})
    target = plan['target']
    return {'contributed': money(actual), 'remaining': money(max(Decimal(0), target-actual)),
            'completion_pct': money(actual / target * 100), 'completed_months': completed_months,
            'missed_months': missed, 'planned_total': money(monthly*plan['months']),
            'remaining_budget': money(plan['monthly_income'] - monthly - plan['personal_expenses'] - plan['learning_expenses'] - plan['emergency_expenses']),
            'calculation_date': today.isoformat(),
            'assumptions': 'Actual contributions are user-entered. Planned contributions are not guaranteed. No investment returns are included.'}
