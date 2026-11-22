"""Validated point-in-time panel and deliberately synthetic reproducibility fixture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

import numpy as np


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('all timestamps must have timezone offsets')
    return parsed.astimezone(timezone.utc)


def content_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate(dataset: dict) -> None:
    required = ('dataset_version', 'dataset_kind', 'universe_source', 'license', 'membership_includes_delisted', 'delisting_returns_complete', 'rows')
    if any(key not in dataset for key in required) or not all(dataset[k] for k in ('dataset_version', 'universe_source', 'license')):
        raise ValueError('dataset provenance and rows are required')
    if dataset['dataset_kind'] not in ('synthetic', 'historical'):
        raise ValueError('dataset_kind must be synthetic or historical')
    if dataset['membership_includes_delisted'] is not True or dataset['delisting_returns_complete'] is not True:
        raise ValueError('missing point-in-time universe/delisting coverage attestation')
    if not isinstance(dataset['rows'], list) or not dataset['rows']:
        raise ValueError('dataset must contain rows')
    seen = set()
    for row in dataset['rows']:
        if not row.get('entity_id') or row.get('member_at_origin') is not True:
            raise ValueError('rows require a point-in-time eligible member')
        origin = parse_time(row['origin'])
        if parse_time(row['membership_available_at']) > origin or parse_time(row['membership_retrieved_at']) > origin:
            raise ValueError('universe membership was not known by origin')
        if parse_time(row['label_available_at']) <= origin or parse_time(row['label_retrieved_at']) < parse_time(row['label_available_at']):
            raise ValueError('label availability must follow origin and retrieval must not precede availability')
        key = (row['entity_id'], origin)
        if key in seen:
            raise ValueError('duplicate entity/origin')
        seen.add(key)
        for key_name in ('signal', 'future_return', 'scenario_shock'):
            if not math.isfinite(float(row[key_name])):
                raise ValueError(f'non-finite {key_name}')
        if row.get('scenario_truth') is not None and not math.isfinite(float(row['scenario_truth'])):
            raise ValueError('invalid scenario_truth')
        if abs(float(row['scenario_shock'])) > 1:
            raise ValueError('scenario_shock exceeds the declared range')
        if row['future_return'] < -1:
            raise ValueError('return cannot be below -100%; encode terminal proceeds explicitly')
        if row.get('delisted_after_origin') and row.get('exit_return_included') is not True:
            raise ValueError('missing delisting exit return')
        if not isinstance(row.get('evidence'), list) or not row['evidence']:
            raise ValueError('at least one point-in-time evidence record required')
        ids = set()
        for item in row['evidence']:
            if not all(item.get(k) for k in ('id', 'source', 'metric', 'unit', 'published_at', 'available_at', 'retrieved_at', 'version_identifier', 'validation_status')) or item['id'] in ids:
                raise ValueError('evidence identifiers, provenance and units must be present and unique')
            ids.add(item['id'])
            if item['validation_status'] != 'valid' or parse_time(item['published_at']) > parse_time(item['available_at']) or not math.isfinite(float(item['value'])) or parse_time(item['available_at']) > origin or parse_time(item['retrieved_at']) > origin:
                raise ValueError('future, missing or invalid evidence')
        if not any(e['metric'] == 'signal' and e['unit'] == 'fraction' and math.isclose(float(e['value']), float(row['signal']), rel_tol=0, abs_tol=1e-12) for e in row['evidence']):
            raise ValueError('signal must match an as-of evidence record')
        refs = row.get('verification_references')
        if not isinstance(refs, list):
            raise ValueError('verification references must be a list, possibly empty')
        for ref in refs:
            if not all(ref.get(k) for k in ('id', 'source', 'metric', 'unit', 'published_at', 'available_at', 'retrieved_at', 'version_identifier', 'validation_status')) or not math.isfinite(float(ref['value'])):
                raise ValueError('invalid verification reference')
            if ref['validation_status'] != 'valid' or parse_time(ref['published_at']) > parse_time(ref['available_at']) or parse_time(ref['available_at']) > origin or parse_time(ref['retrieved_at']) > origin:
                raise ValueError('future verification reference')


def split(dataset: dict, train_end: str, validation_end: str, test_end: str) -> tuple[dict, dict]:
    """Outcome availability is purged at each split boundary, including test end."""
    validate(dataset)
    train, validation, test = map(parse_time, (train_end, validation_end, test_end))
    if not train < validation < test:
        raise ValueError('split cutoffs must be strictly increasing')
    parts = {'train': [], 'validation': [], 'test': []}
    excluded = {'outside_window': 0, 'unmatured_at_boundary': 0}
    for row in sorted(dataset['rows'], key=lambda x: (parse_time(x['origin']), x['entity_id'])):
        origin, label_time = parse_time(row['origin']), max(parse_time(row['label_available_at']), parse_time(row['label_retrieved_at']))
        if origin <= train:
            name, boundary = 'train', train
        elif origin <= validation:
            name, boundary = 'validation', validation
        elif origin <= test:
            name, boundary = 'test', test
        else:
            excluded['outside_window'] += 1
            continue
        if label_time > boundary:
            excluded['unmatured_at_boundary'] += 1
            continue
        parts[name].append(row)
    if any(not part for part in parts.values()):
        raise ValueError('every split must have at least one mature observation')
    return parts, excluded


def synthetic(seed: int = 42, days: int = 60) -> tuple[dict, dict]:
    if days < 30:
        raise ValueError('synthetic fixture requires >=30 dates')
    rng = np.random.default_rng(seed)
    start = datetime(2020, 1, 1, 16, tzinfo=timezone.utc)
    rows = []
    for i in range(days):
        origin = start + timedelta(days=i)
        for asset in range(4):
            # D leaves part-way through test; its terminal loss is included, not dropped.
            exit_day = days * 4 // 5 if asset == 3 else days - 1
            if i > exit_day:
                continue
            signal = float(rng.normal(0, 0.02))
            realized = float(np.clip(0.2 * signal + rng.normal(0, 0.015) - (0.15 if asset == 3 and i == exit_day else 0), -0.9, 1))
            known = (origin - timedelta(hours=1)).isoformat()
            eid = f'e-{i}-{asset}'
            evidence = [{'id': eid, 'source': 'synthetic-input', 'metric': 'signal', 'value': signal,
                         'unit': 'fraction', 'published_at': known, 'available_at': known, 'retrieved_at': known, 'version_identifier': 'v1', 'validation_status': 'valid'}]
            refs = [{'id': f'ref-{i}-{asset}', 'source': 'synthetic-reference', 'metric': 'signal', 'value': signal,
                     'unit': 'fraction', 'published_at': known, 'available_at': known, 'retrieved_at': known, 'version_identifier': 'v1', 'validation_status': 'valid'}]
            rows.append({'entity_id': f'asset-{asset}', 'origin': origin.isoformat(), 'membership_available_at': known,
                         'membership_retrieved_at': known, 'member_at_origin': True, 'signal': signal, 'evidence': evidence,
                         'verification_references': refs, 'future_return': realized,
                         'label_available_at': (origin + timedelta(hours=24)).isoformat(),
                         'label_retrieved_at': (origin + timedelta(hours=24)).isoformat(),
                         'delisted_after_origin': bool(asset == 3 and i == exit_day), 'exit_return_included': True,
                         'scenario_shock': 0.01, 'scenario_truth': 0.003})
    ds = {'dataset_version': f'synthetic-panel-v1-seed-{seed}-days-{days}', 'dataset_kind': 'synthetic',
          'universe_source': 'synthetic full historical membership including exits', 'license': 'synthetic research fixture',
          'membership_includes_delisted': True, 'delisting_returns_complete': True, 'rows': rows}
    config = {'train_end': (start + timedelta(days=days//2)).isoformat(),
              'validation_end': (start + timedelta(days=days*3//4)).isoformat(),
              'test_end': (start + timedelta(days=days)).isoformat()}
    return ds, config
