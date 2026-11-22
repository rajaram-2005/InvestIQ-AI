"""Destructive integration test: runs ONLY on explicitly designated disposable DB."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import os
import uuid

import psycopg
import pytest
from investiq.evidence.core import Source, Document
from investiq.evidence.store import add_embedding, create_snapshot, get_snapshot, ingest, link_claims, search_claims

DSN = os.getenv('INVESTIQ_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN or os.getenv('INVESTIQ_DISPOSABLE_TEST_DB') != '1',
    reason='requires explicitly designated disposable Postgres+pgvector database')


@pytest.fixture(scope='module')
def db():
    conn = psycopg.connect(DSN, autocommit=True)
    name, version = conn.execute('select current_database(), version()').fetchone()
    if not (name.startswith('investiq_test_') or ('PGlite' in version and name == 'postgres')):
        conn.close()
        pytest.fail('refusing destructive setup: database is not named investiq_test_* or disposable PGlite')
    conn.execute('drop schema if exists investiq_evidence cascade')
    conn.execute('drop schema if exists auth cascade')
    conn.execute('create schema auth')
    conn.execute('create table auth.users (id uuid primary key)')
    conn.execute("""create function auth.uid() returns uuid language sql stable as $$
                    select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$""")
    conn.execute("""do $$ begin
        if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
        if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
        if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
        end $$""")
    conn.execute('grant usage on schema auth to authenticated, service_role')
    conn.execute(Path('supabase/migrations/20261122000100_evidence_infrastructure.sql').read_text())
    yield conn
    conn.close()


def src():
    return Source(str(uuid.uuid4()), 'Fixture provider', 'synthetic://test/provider', 'fixture only',
                  'local test license', True, True, False, 'v1')


def doc(kind='fund_nav', media='text/csv'):
    return Document(str(uuid.uuid4()), kind, 'synthetic://test/file', media,
                    '2000-01-01T00:00:00Z','2000-01-01T00:00:00Z',str(uuid.uuid4()))


def nav(value, version, supersedes=''):
    return ('entity_id,period_start,period_end,published_at,available_at,currency,unit,version_identifier,nav,supersedes_nav_id\n'
            f'F,2000-01-01,2000-01-01,2000-01-02T00:00:00Z,2000-01-02T00:00:00Z,USD,USD/unit,{version},{value},{supersedes}\n').encode()


def test_ingest_snapshots_restated_values_and_quality(db):
    owner = str(uuid.uuid4()); db.execute('insert into auth.users(id) values (%s)', (owner,))
    source = src()
    before = db.execute('select clock_timestamp()').fetchone()[0].isoformat()
    first = ingest(db, owner, source, doc(), nav('10.00','v1'))
    between = db.execute('select clock_timestamp()').fetchone()[0].isoformat()
    original = create_snapshot(db, owner, between)
    early = create_snapshot(db, owner, before)
    assert early['manifest']['observations'] == []
    assert original['manifest']['observations'][0]['original_value'] == '10.00'
    old_id = first['observation_ids'][0]
    second = ingest(db, owner, source, doc(), nav('11.00','v2',old_id))
    after = db.execute('select clock_timestamp()').fetchone()[0].isoformat()
    revised = create_snapshot(db, owner, after)
    assert revised['manifest']['observations'][0]['original_value'] == '11.00'
    assert get_snapshot(db, owner, original['snapshot_id']) == original['manifest']
    assert create_snapshot(db, owner, between)['manifest']['observations'][0]['original_value'] == '10.00'
    ingest(db, owner, source, doc(), nav('-1','v3',second['observation_ids'][0]))
    cutoff = db.execute('select clock_timestamp()').fetchone()[0].isoformat()
    bad = create_snapshot(db, owner, cutoff)['manifest']
    assert not bad['observations'] and bad['excluded_latest'][0]['original_value'] == '-1'
    with pytest.raises(psycopg.Error):
        db.execute('update investiq_evidence.observations set original_value=%s where id=%s', ('99',old_id))
    with pytest.raises(ValueError):
        create_snapshot(db, owner, '2999-01-01T00:00:00Z')


def test_private_graph_vector_and_rls(db):
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    db.execute('insert into auth.users(id) values (%s),(%s)', (owner, other))
    source = src()
    raw = b'Revenue was 100 USD. A dividend of 2 USD was declared.'
    result = ingest(db, owner, source, doc('corporate_announcement','text/plain'), raw)
    assert len(result['claim_ids']) == 2 and len(result['announcement_ids']) == 1
    relation = link_claims(db, owner, result['claim_ids'][0], result['claim_ids'][1], 'references', 'same announcement')
    embedding = add_embedding(db, owner, result['claim_ids'][0], 'fixture-model', [0.1]*384)
    cutoff = db.execute('select clock_timestamp()').fetchone()[0].isoformat()
    snapshot = create_snapshot(db, owner, cutoff)['manifest']
    assert len(snapshot['documents']) == 1 and len(snapshot['claims']) == 2 and len(snapshot['announcements']) == 1
    assert snapshot['relations'][0]['id'] == relation and snapshot['embeddings'][0]['id'] == embedding
    matches = search_claims(db, owner, 'fixture-model', [0.1]*384, cutoff)
    assert len(matches) == 1 and matches[0]['document_id'] == result['document_id']
    assert search_claims(db, other, 'fixture-model', [0.1]*384, cutoff) == []
    assert search_claims(db, owner, 'fixture-model', [0.1]*384, '2000-01-01T00:00:00Z') == []
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        link_claims(db, other, result['claim_ids'][0], result['claim_ids'][1], 'supports', 'illegal cross-owner edge')
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub', %s, true)", (other,))
        assert db.execute('select count(*) from investiq_evidence.documents').fetchone()[0] == 0
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub', %s, true)", (owner,))
        assert db.execute('select count(*) from investiq_evidence.documents').fetchone()[0] == 1
        assert db.execute('select count(*) from investiq_evidence.claim_embeddings').fetchone()[0] == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute('delete from investiq_evidence.documents')


def test_raw_storage_rights_and_trigger(db):
    owner = str(uuid.uuid4()); db.execute('insert into auth.users(id) values (%s)', (owner,))
    source = replace(src(), allow_raw_storage=False)
    result = ingest(db, owner, source, doc('financial_document','text/plain'), b'Revenue was 100 USD.')
    assert result['claim_ids'] == [] and result['warnings']
    row = db.execute('select raw_content, extracted_text from investiq_evidence.documents where id=%s', (result['document_id'],)).fetchone()
    assert row == (None, None)
