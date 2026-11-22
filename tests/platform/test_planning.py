from datetime import date
from decimal import Decimal as D
from investiq.platform import planning

def test_savings_and_example_budget():
    assert planning.savings(D('17500'),24)['total']=='420000.00'
    assert planning.savings(D('0.10'),3)['total']=='0.30'

def test_compound_and_sip_zero_and_nonzero():
    assert planning.compound(D('1000'),D('12'),12)['total']=='1126.83'
    assert planning.sip(D('1000'),D('0'),12)['total']=='12000.00'
    assert planning.sip(D('1000'),D('12'),12)['total']=='12682.50'
    assert planning.savings(D('0'),0)['total']=='0.00'

def test_mission_progress_respects_start_and_completed_months():
    plan={'start_date':date(2025,1,1),'months':24,'monthly_contribution':D('17500'), 'target':D('420000'), 'monthly_income':D('23000'), 'personal_expenses':D('3500'), 'learning_expenses':D('1000'), 'emergency_expenses':D('1000')}
    records=[{'amount':D('17500'), 'contribution_date':date(2025,1,5)}, {'amount':D('5000'), 'contribution_date':date(2025,2,5)}]
    r=planning.mission_progress(plan, records, date(2025,3,1))
    assert r['contributed']=='22500.00' and r['remaining']=='397500.00'
    assert r['remaining_budget']=='0.00' and r['planned_total']=='420000.00'
    assert r['missed_months']==[{'month':'2025-02','contributed':'5000.00','planned':'17500.00'}]
    assert planning.mission_progress(plan, [], date(2024,12,1))['missed_months']==[]
