import copy
from datetime import timedelta
import pytest
from investiq.lab.data import parse_time, split, synthetic, validate


def test_chronology_and_label_maturity_purge():
    ds, config = synthetic(3, 30)
    parts, excluded = split(ds, **config)
    assert excluded['unmatured_at_boundary'] > 0
    for name, end in [('train', config['train_end']), ('validation', config['validation_end']), ('test', config['test_end'])]:
        assert all(parse_time(r['label_available_at']) <= parse_time(end) and parse_time(r['label_retrieved_at']) <= parse_time(end) for r in parts[name])
    assert max(parse_time(r['origin']) for r in parts['train']) < min(parse_time(r['origin']) for r in parts['validation'])
    assert any(r['delisted_after_origin'] for r in parts['test'])


def test_future_evidence_and_survivorship_fail_closed():
    ds, _ = synthetic(3, 30)
    row = ds['rows'][0]
    bad = copy.deepcopy(ds); bad['rows'][0]['evidence'][0]['retrieved_at'] = '2099-01-01T00:00:00Z'
    with pytest.raises(ValueError): validate(bad)
    bad = copy.deepcopy(ds); bad['rows'][0]['signal'] += 1
    with pytest.raises(ValueError): validate(bad)
    bad = copy.deepcopy(ds); bad['membership_includes_delisted'] = False
    with pytest.raises(ValueError): validate(bad)
    bad = copy.deepcopy(ds); bad['rows'][0]['member_at_origin'] = 'true'
    with pytest.raises(ValueError): validate(bad)
    bad = copy.deepcopy(ds); next(r for r in bad['rows'] if r['delisted_after_origin'])['exit_return_included'] = False
    with pytest.raises(ValueError): validate(bad)
    bad = copy.deepcopy(ds); bad['rows'].append(row)
    with pytest.raises(ValueError): validate(bad)


def test_late_retrieval_purges_even_if_label_public():
    ds, cfg = synthetic(2, 30)
    row = ds['rows'][0]
    row['label_retrieved_at'] = (parse_time(cfg['train_end']) + timedelta(days=1)).isoformat()
    parts, _ = split(ds, **cfg)
    assert not any(r['origin'] == row['origin'] and r['entity_id'] == row['entity_id'] for r in parts['train'])
