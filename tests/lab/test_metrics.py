import pytest
from investiq.lab.metrics import claim_checks, expected_calibration_error, portfolio, paired_bootstrap, holm
import numpy as np


def test_independent_verification_and_missing_references():
    claims = [{'evidence_id':'a','metric':'signal','unit':'fraction','value':1}]
    evidence = [{'id':'a','metric':'signal','unit':'fraction','value':1,'source':'provider'}]
    refs = [{'metric':'signal','unit':'fraction','value':1,'source':'audit'}]
    assert claim_checks(claims,evidence,refs)['supported'] == 1
    assert claim_checks(claims,evidence,[])['unsupported'] == 1
    assert claim_checks(claims,evidence,[{**refs[0], 'source':'provider'}])['unsupported'] == 1
    assert claim_checks([{**claims[0], 'value':2}],evidence,refs)['valid_citations'] == 0


def test_cost_turnover_drawdown_and_no_trading():
    records = [{'origin':'1','entity_id':'a','probability':.6,'outcome':.1},
               {'origin':'2','entity_id':'a','probability':.6,'outcome':0.0}]
    result = portfolio(records, 10)
    assert result['terminal_wealth'] == pytest.approx((1 + .025 - .00025) * (1 - .00025))
    assert result['total_turnover'] == pytest.approx(.5)
    assert result['maximum_drawdown'] == pytest.approx(.00025)
    assert result['risk_constraint_violations'] == 0
    no_trade = portfolio([{**r, 'probability':.1} for r in records], 10)
    assert no_trade['terminal_wealth'] == 1 and no_trade['maximum_drawdown'] == 0
    assert expected_calibration_error(np.array([0,1]),np.array([0,1])) == 0


def test_clustered_paired_bootstrap_and_holm():
    a = [{'origin':str(i),'entity_id':str(j),'probability':.8,'outcome':.1} for i in range(10) for j in range(3)]
    b = [{**r,'probability':.5} for r in a]
    result = paired_bootstrap(a,b,seed=4,samples=200,block_length=3)
    assert result['difference'] == pytest.approx(-.21)
    assert result['dates'] == 10 and result['common_rows'] == 30
    assert result == paired_bootstrap(a,b,seed=4,samples=200,block_length=3)
    assert holm({'a':.01,'b':.04,'c':.03}) == {'a':.03,'c':.06,'b':.06}
