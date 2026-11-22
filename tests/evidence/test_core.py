from dataclasses import replace
import json
import pytest
from investiq.evidence.core import Source, Document, extract_observations, extract_text, extract_claims, relation_graph, stable_hash, timestamp
from investiq.evidence.store import provenance_report

SOURCE = Source("aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa", "Example provider", "synthetic://provider", "test-only", "synthetic fixture", True, True, False, "v1")

def document(kind="market_prices", media="text/csv"):
    return Document("bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb", kind, "synthetic://provider/document", media, "2020-01-01T00:00:00Z", "2020-01-01T00:00:00Z", "d1")


def csv_text(value="00100.00"):
    return ("entity_id,period_start,period_end,published_at,available_at,currency,unit,version_identifier,open,high,low,close,volume\n"
            f"ABC,2020-01-01,2020-01-01,2020-01-02T00:00:00Z,2020-01-02T00:00:00Z,USD,USD/share,v1,99,101,98,{value},1000\n").encode()


def test_stock_ingestion_preserves_original_and_provenance():
    records = extract_observations(csv_text(), SOURCE, document())
    assert len(records) == 5
    close = next(o for o in records if o.metric == 'close')
    assert close.original_value == "00100.00" and close.numeric_value == "100.00"
    assert close.entity_id == 'ABC' and close.currency == 'USD' and close.validation_status == 'valid'
    assert close.published_at.startswith('2020-01-02')
    assert close.logical_key == extract_observations(csv_text('100'), SOURCE, document())[3].logical_key


def test_nav_financial_statement_and_quarantine():
    raw = b'entity_id,period_start,period_end,published_at,available_at,currency,unit,version_identifier,nav\nFUND1,2020-01-01,2020-01-01,2020-01-02T00:00:00Z,2020-01-02T00:00:00Z,INR,INR/unit,v1,-1\n'
    nav = extract_observations(raw, SOURCE, document('fund_nav'))[0]
    assert nav.validation_status == 'quarantined' and nav.validation_errors == ('price_or_nav_not_positive',)
    statement = b'{"rows":[{"entity_id":"ABC","metric":"revenue","value":1.20e3,"currency":"USD","unit":"USD","period_start":"2020-01-01","period_end":"2020-03-31","published_at":"2020-04-30T00:00:00Z","available_at":"2020-04-30T00:00:00Z","version_identifier":"filing-v1"}]}'
    row = extract_observations(statement, SOURCE, document('financial_statement', 'application/json'))[0]
    assert row.original_value == '1.20e3' and row.period_end == '2020-03-31'


def test_bad_times_prices_licenses_and_inputs():
    with pytest.raises(ValueError): timestamp('2020-01-01')
    with pytest.raises(ValueError): extract_observations(csv_text('200'), SOURCE, document())
    with pytest.raises(ValueError): extract_observations(csv_text(), replace(SOURCE, allow_derived_storage=False), document())
    with pytest.raises(ValueError): extract_observations(csv_text(), replace(SOURCE, source_uri='https://x.test/path?api_key=secret'), document())
    with pytest.raises(ValueError): extract_observations(csv_text(), SOURCE, replace(document(), available_at='2019-01-01T00:00:00Z'))
    with pytest.raises(ValueError): extract_observations(csv_text().replace(b'USD,USD/share', b'usd,USD/share'), SOURCE, document())
    with pytest.raises(ValueError): extract_observations(csv_text().replace(b'2020-01-02T00:00:00Z', b'2019-01-02T00:00:00Z'), SOURCE, document())


def test_exact_document_and_announcement_claims_graph():
    doc = document('corporate_announcement', 'text/plain')
    text = extract_text('Revenue rose 12.5 percent. Guidance is unchanged. Dividend was 2 USD.\n'.encode(), doc)
    claims = extract_claims(text)
    assert len(claims) == 2 and claims[0].quote == 'Revenue rose 12.5 percent.'
    assert all(text[c.char_start:c.char_end] == c.quote and c.verification_status == 'unverified' for c in claims)
    graph = relation_graph(['a','b'], [{'from':'a','to':'b','relation':'contradicts','rationale':'different values'}])
    assert graph['a']['b'][0]['relation'] == 'contradicts'
    with pytest.raises(ValueError): relation_graph(['a'], [{'from':'a','to':'b','relation':'supports','rationale':'x'}])


def test_report_is_deterministic_and_labels_restrictions():
    manifest = {'as_of':'2020-01-01T00:00:00+00:00', 'observations':[{'provider':'p','allow_redistribution':False}], 'excluded_latest':[{}]}
    report = provenance_report(manifest)
    assert report['valid_count'] == 1 and report['quarantined_latest_count'] == 1 and report['restricted_observations'] == 1
    assert report['manifest_sha256'] == stable_hash(manifest)
    assert stable_hash({'b':1,'a':2}) == stable_hash({'a':2,'b':1})


def test_sec_companyfacts_requires_explicit_accession_and_release_times():
    wrapper = {"companyfacts":{"cik":123, "facts":{"us-gaap":{"Revenues":{"units":{"USD":[
        {"accn":"A-1", "start":"2019-01-01", "end":"2019-12-31", "val":1234},
        {"accn":"A-2", "start":"2019-01-01", "end":"2019-12-31", "val":1400}]}}}}},
        "accession":"A-1", "published_at":"2020-01-02T00:00:00Z", "available_at":"2020-01-02T00:00:00Z"}
    parsed = extract_observations(json.dumps(wrapper).encode(), SOURCE, document('financial_statement','application/json'))
    assert len(parsed) == 1 and parsed[0].entity_id == 'CIK:0000000123' and parsed[0].metric == 'us-gaap:Revenues'
    assert parsed[0].original_value == '1234' and parsed[0].version_identifier == 'A-1'
    invalid = json.loads(json.dumps(wrapper))
    invalid['companyfacts']['facts']['us-gaap']['Revenues']['units']['widgets'] = [{'accn':'A-1','end':'2019-12-31','val':1}]
    with pytest.raises(ValueError): extract_observations(json.dumps(invalid).encode(), SOURCE, document('financial_statement','application/json'))
    wrapper.pop('available_at')
    with pytest.raises(ValueError): extract_observations(json.dumps(wrapper).encode(), SOURCE, document('financial_statement','application/json'))


def test_announcement_extraction_and_preview():
    from investiq.evidence.core import extract_announcements, preview
    raw = b'The board announced a dividend of 2 USD. Guidance remains unchanged.'
    events = extract_announcements(raw.decode())
    assert {item['event_type'] for item in events} == {'dividend', 'guidance'}
    assert all(raw.decode()[e['char_start']:e['char_end']] == e['quote'] for e in events)
    report = preview(raw, SOURCE, document('corporate_announcement','text/plain'))
    assert report['announcement_count'] == 2 and report['retrieved_at'] is None
    restricted = preview(raw, replace(SOURCE, allow_raw_storage=False), document('corporate_announcement','text/plain'))
    assert restricted['claim_count'] == 0 and restricted['announcement_count'] == 0
