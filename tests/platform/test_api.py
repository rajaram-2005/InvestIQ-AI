"""Platform integration against an explicitly disposable PostgreSQL-compatible DB.
Authentication is overridden; this is not a Supabase Auth end-to-end test.
"""
import os
from pathlib import Path
from contextlib import contextmanager
import investiq.platform.api as api
from psycopg.rows import dict_row
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from investiq.platform.api import app, current_user

DSN = os.getenv('INVESTIQ_TEST_DATABASE_URL')
pytestmark = pytest.mark.skipif(not DSN or os.getenv('INVESTIQ_DISPOSABLE_TEST_DB') != '1', reason='requires explicitly designated disposable database')

@pytest.fixture(scope='module')
def setup_db():
    c = psycopg.connect(DSN, autocommit=True)
    name, version = c.execute('select current_database(), version()').fetchone()
    if not (name.startswith('investiq_test_') or ('PGlite' in version and name == 'postgres')):
        pytest.fail('refusing destructive setup outside a disposable database')
    c.execute('drop schema if exists investiq_app cascade')
    c.execute('drop schema if exists investiq_evidence cascade')
    c.execute('drop schema if exists auth cascade')
    c.execute('create schema auth; create table auth.users (id uuid primary key)')
    c.execute("create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$")
    c.execute("""do $$ begin
      if not exists (select 1 from pg_roles where rolname='anon') then create role anon nologin; end if;
      if not exists (select 1 from pg_roles where rolname='authenticated') then create role authenticated nologin; end if;
      if not exists (select 1 from pg_roles where rolname='service_role') then create role service_role nologin bypassrls; end if;
    end $$""")
    c.execute('grant usage on schema auth to authenticated, service_role')
    c.execute(Path('supabase/migrations/20261122000100_evidence_infrastructure.sql').read_text())
    c.execute(Path('supabase/migrations/20261122000200_unified_platform.sql').read_text())
    c.execute(Path('supabase/migrations/20261122000300_savings_mission.sql').read_text())
    c.row_factory = dict_row
    yield c
    c.close()

@pytest.fixture
def clients(setup_db, monkeypatch):
    owner, other = str(uuid4()), str(uuid4())
    c = setup_db
    c.execute('insert into auth.users(id) values (%s),(%s)', (owner,other))
    @contextmanager
    def test_connection(user):
        with c.transaction():
            c.execute('set local role authenticated')
            c.execute("select set_config('request.jwt.claim.sub',%s,true)", (user,))
            yield c
    monkeypatch.setattr(api, 'connection', test_connection)
    app.dependency_overrides[current_user] = lambda: owner
    with TestClient(app, headers={'x-investiq-request':'1'}) as client:
        yield client, owner, other, c
    app.dependency_overrides.clear()

def test_private_profile_consent_and_scenario(setup_db, clients):
    client, owner, other, db = clients
    data = {'income':100000, 'expenses':60000, 'assets':500000, 'liabilities':100000, 'uncertainty_pct':10}
    assert client.post('/v1/profile', json=data).status_code == 403
    assert client.post('/v1/consent', json={'granted':True}).status_code == 200
    response = client.post('/v1/profile', json=data)
    assert response.status_code == 200, response.text
    assert response.json()['profile']['net_worth'] == '400000'
    result = client.get('/v1/profile')
    assert result.status_code == 200, result.text
    assert result.json()['tfsf']['model_version'] == 'tfsf-diagonal-v1'
    scenario = client.post('/v1/scenarios/budget', json={'income_change':5000,'expense_change':2000})
    assert scenario.status_code == 200, scenario.text
    assert scenario.json()['scenario_surplus'] == '43000'
    assert client.post('/v1/scenarios/budget', json={'income_change':-100001,'expense_change':0}).status_code == 422
    assert len(client.get('/v1/audit').json()['events']) >= 3
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub',%s,true)",(other,))
        assert db.execute('select count(*) from investiq_app.financial_snapshots').fetchone()['count'] == 0
    assert client.post('/v1/consent',json={'granted':False}).status_code == 200
    assert client.get('/v1/profile').status_code == 403
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub',%s,true)",(owner,))
        assert db.execute('select count(*) from investiq_app.financial_snapshots').fetchone()['count'] == 0


def test_unconfigured_empty_workflows_and_request_verification(clients):
    client, _, _, db = clients
    assert client.get('/v1/market/stocks').json()['items'] == []
    assert client.get('/v1/market/funds').json()['items'] == []
    assert client.get('/v1/evidence').json()['documents'] == []
    assert client.post('/v1/reports').status_code == 409
    assert client.post('/v1/assistant',json={'question':'revenue growth'}).json()['matches'] == []
    assert client.post('/v1/forecasts',json={'event':'x','probability':0.5,'target_at':'2000-01-01T00:00:00Z','evidence_claim_id':str(uuid4())}).status_code == 422
    with TestClient(app) as unverified:
        assert unverified.post('/v1/consent',json={'granted':True}).status_code == 403


def test_verified_evidence_forecast_and_isolation(clients):
    from datetime import datetime, timezone, timedelta
    import hashlib
    client, owner, other, db = clients
    source, doc, claim = (str(uuid4()) for _ in range(3))
    text = 'A fixture claim for testing.'
    db.execute('''insert into investiq_evidence.sources(id,owner_id,provider,source_uri,license,license_reference,allow_raw_storage,allow_derived_storage,version_identifier)
               values(%s,%s,'Fixture','synthetic://provider','test','test',true,true,'1')''', (source, owner))
    db.execute('''insert into investiq_evidence.documents(id,owner_id,source_id,original_source_uri,document_type,media_type,content_sha256,raw_content,extracted_text,published_at,available_at,version_identifier)
               values(%s,%s,%s,'https://example.org/fixture','financial_document','text/plain',%s,%s,%s,'2000-01-01','2000-01-01','1')''', (doc,owner,source,hashlib.sha256(text.encode()).hexdigest(),text.encode(),text))
    db.execute('''insert into investiq_evidence.claims(id,owner_id,document_id,quote,char_start,char_end,extractor_version,verification_status)
                values(%s,%s,%s,%s,0,%s,'test','human_verified')''', (claim,owner,doc,text,len(text)))
    assert len(client.get('/v1/evidence').json()['documents']) == 1
    assert client.get('/v1/evidence/'+doc).json()['claims'][0]['quote'] == text
    assert len(client.post('/v1/assistant',json={'question':'fixture claim'}).json()['matches']) == 1
    assert len(client.post('/v1/reports').json()['claims']) == 1
    target = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    forecast = client.post('/v1/forecasts',json={'event':'Test event','probability':0.7,'target_at':target,'evidence_claim_id':claim})
    assert forecast.status_code == 200, forecast.text
    fid = forecast.json()['id']
    assert len(client.get('/v1/forecasts').json()['forecasts']) == 1
    assert client.post('/v1/forecasts/'+fid+'/resolve',json={'outcome':True,'evidence_claim_id':claim}).status_code == 409
    app.dependency_overrides[current_user] = lambda: other
    assert client.get('/v1/evidence/'+doc).status_code == 404
    assert client.get('/v1/forecasts').json()['forecasts'] == []
    assert client.post('/v1/forecasts',json={'event':'Other','probability':0.5,'target_at':target,'evidence_claim_id':claim}).status_code == 422


def test_mission_contributions_decimal_and_isolation(clients):
    from datetime import date
    client, owner, other, db = clients
    example={'title':'Two-year mission','start_date':'2025-01-01','months':24,'target':'420000.00',
             'monthly_contribution':'17500.00','monthly_income':'23000.00','personal_expenses':'3500.00',
             'learning_expenses':'1000.00','emergency_expenses':'1000.00','benefits_confirmed':False}
    assert client.post('/v1/goals',json=example).status_code == 403
    assert client.post('/v1/consent',json={'granted':True}).status_code == 200
    created = client.post('/v1/goals',json=example)
    assert created.status_code == 200, created.text
    mission_id = created.json()['mission']['id']
    assert client.post('/v1/savings/contributions',json={'mission_id':mission_id,'amount':'17500.00','contribution_date':'2025-01-03'}).status_code == 200
    assert client.post('/v1/savings/contributions',json={'mission_id':mission_id,'amount':'1','contribution_date':'2999-01-01'}).status_code == 422
    progress = client.get('/v1/goals/progress').json()
    assert progress['progress']['contributed']=='17500.00'
    assert client.post('/v1/savings/contributions',json={'mission_id':mission_id,'amount':'0.001','contribution_date':'2025-01-03'}).status_code == 422
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub',%s,true)",(owner,))
        with pytest.raises(psycopg.Error):
            db.execute('update investiq_app.savings_missions set target=1 where id=%s', (mission_id,))
    assert progress['progress']['planned_total']=='420000.00'
    assert client.post('/v1/calculators/savings',json={'monthly_contribution':'17500.00','months':24}).json()['total']=='420000.00'
    assert client.post('/v1/calculators/sip',json={'monthly_contribution':'1000','annual_rate_pct':'0','months':12}).json()['total']=='12000.00'
    assert client.post('/v1/calculators/compound-interest',json={'principal':'1000','annual_rate_pct':'12','months':12}).json()['total']=='1126.83'
    app.dependency_overrides[current_user]=lambda: other
    assert client.post('/v1/consent',json={'granted':True}).status_code == 200
    assert client.get('/v1/goals/progress').json()['mission'] is None
    assert client.post('/v1/savings/contributions',json={'mission_id':mission_id,'amount':'10','contribution_date':'2025-01-03'}).status_code == 404
    app.dependency_overrides[current_user]=lambda: owner
    assert client.post('/v1/consent',json={'granted':False}).status_code == 200
    assert client.get('/v1/goals/progress').status_code == 403
    with db.transaction():
        db.execute('set local role authenticated')
        db.execute("select set_config('request.jwt.claim.sub',%s,true)",(owner,))
        assert db.execute('select count(*) from investiq_app.savings_missions').fetchone()['count'] == 0
