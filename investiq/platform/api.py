"""Private FastAPI research workspace. No demo data or privileged browser credentials."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from typing import Annotated
from uuid import UUID

import httpx
import psycopg
from psycopg.rows import dict_row
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from investiq.prototypes import eccr, tfsf

app = FastAPI(title="InvestIQ Research API", version="0.1.0", docs_url=None, redoc_url=None)
COOKIE = "__Host-investiq_session"


def settings():
    auth_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    # Explicitly opt in to the official local Supabase stack; never allow remote HTTP.
    local_auth = os.getenv("INVESTIQ_ALLOW_LOCAL_AUTH") == "1" and auth_url == "http://127.0.0.1:54321"
    return {"auth": bool((auth_url.startswith("https://") or local_auth) and os.getenv("SUPABASE_ANON_KEY")),
            "database": bool(os.getenv("INVESTIQ_DATABASE_URL"))}


@app.middleware("http")
async def secure_headers(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.headers.get("x-investiq-request") != "1":
        return JSONResponse({"detail": "Request verification required"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/v1/status")
def status():
    s = settings()
    return {"authentication_configured": s["auth"], "database_configured": s["database"],
            "market_feed": "not_configured", "assistant_mode": "verified_evidence_retrieval", "version": "0.1.0"}


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


@app.post("/v1/session")
async def login(payload: Login, response: Response):
    if not settings()["auth"]:
        raise HTTPException(503, "Authentication is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            result = await client.post(os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/token?grant_type=password",
                 headers={"apikey": os.environ["SUPABASE_ANON_KEY"]}, json={"email": payload.email, "password": payload.password})
        if result.status_code != 200:
            raise HTTPException(401, "Unable to sign in.")
        data = result.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise HTTPException(401, "Unable to sign in.")
        response.set_cookie(COOKIE, token, secure=True, httponly=True, samesite="lax", path="/", max_age=min(int(data.get("expires_in", 3600)), 3600))
        return {"signed_in": True}
    except httpx.HTTPError:
        raise HTTPException(503, "Authentication service unavailable.") from None


@app.delete("/v1/session")
def logout(response: Response):
    response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    return {"signed_in": False}


async def current_user(session: Annotated[str | None, Cookie(alias=COOKIE)] = None) -> str:
    if not session:
        raise HTTPException(401, "Sign in to access your research workspace.")
    if not settings()["auth"]:
        raise HTTPException(503, "Authentication is not configured.")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            result = await client.get(os.environ["SUPABASE_URL"].rstrip("/") + "/auth/v1/user",
                headers={"apikey": os.environ["SUPABASE_ANON_KEY"], "Authorization": "Bearer " + session})
        if result.status_code != 200:
            raise HTTPException(401, "Session expired. Sign in again.")
        return str(UUID(result.json()["id"]))
    except (httpx.HTTPError, KeyError, ValueError):
        raise HTTPException(503, "Authentication service unavailable.") from None


@contextmanager
def connection(owner: str):
    dsn = os.getenv("INVESTIQ_DATABASE_URL")
    if not dsn:
        raise HTTPException(503, "Research database is not configured.")
    try:
        with psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5) as conn:
            # Backend connection role must be explicitly allowed to assume authenticated.
            # All queries run under RLS, even if the connection role is privileged.
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claim.sub', %s, true)", (owner,))
            conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": owner, "role": "authenticated"}),))
            yield conn
    except HTTPException:
        raise
    except psycopg.Error:
        raise HTTPException(503, "The research database is unavailable or the request could not be saved.") from None


def audit(conn, owner, action, subject=None, details=None):
    conn.execute("insert into investiq_app.audit_events(owner_id,action,subject_id,details) values(%s,%s,%s,%s::jsonb)",
                 (owner, action, subject, json.dumps(details or {})))


def consent_granted(conn, owner):
    row = conn.execute("select granted from investiq_app.consent_events where owner_id=%s order by created_at desc,id desc limit 1", (owner,)).fetchone()
    return bool(row and row["granted"])


def require_consent(conn, owner):
    if not consent_granted(conn, owner):
        raise HTTPException(403, "Consent is required before processing personal financial information.")


User = Annotated[str, Depends(current_user)]


@app.get("/v1/me")
def me(user: User):
    with connection(user) as conn:
        return {"signed_in": True, "consent": consent_granted(conn, user)}


class ConsentInput(BaseModel):
    granted: bool


@app.post("/v1/consent")
def consent(payload: ConsentInput, user: User):
    with connection(user) as conn:
        conn.execute("insert into investiq_app.consent_events(owner_id,granted,policy_version) values(%s,%s,%s)", (user, payload.granted, "2026-11-22-v1"))
        audit(conn, user, "consent_changed", details={"granted": payload.granted, "policy_version": "2026-11-22-v1"})
    return {"consent": payload.granted}


class ProfileInput(BaseModel):
    income: Decimal = Field(ge=0, le=1_000_000_000_000)
    expenses: Decimal = Field(ge=0, le=1_000_000_000_000)
    assets: Decimal = Field(ge=0, le=1_000_000_000_000)
    liabilities: Decimal = Field(ge=0, le=1_000_000_000_000)
    goal: Decimal | None = Field(default=None, ge=0, le=1_000_000_000_000)
    uncertainty_pct: Decimal | None = Field(default=None, gt=0, le=100)


def profile_payload(row):
    if not row:
        return None
    values = {k: str(row[k]) if row[k] is not None else None for k in ("income","expenses","assets","liabilities","goal")}
    return {"id": str(row["id"]), **values, "currency": "INR", "recorded_at": row["created_at"].isoformat(),
            "monthly_surplus": str(row["income"] - row["expenses"]), "net_worth": str(row["assets"] - row["liabilities"]),
            "uncertainty_pct": float(row["uncertainty_pct"]) if row["uncertainty_pct"] is not None else None,
            "basis": "Self-reported values; cash flow = income − expenses, net worth = assets − liabilities. Not investment advice."}


@app.get("/v1/profile")
def profile(user: User):
    with connection(user) as conn:
        require_consent(conn, user)
        row = conn.execute("select * from investiq_app.financial_snapshots where owner_id=%s order by created_at desc,id desc limit 1", (user,)).fetchone()
        estimate = None
        if row and row["uncertainty_pct"] is not None:
            history = conn.execute("select * from investiq_app.financial_snapshots where owner_id=%s and uncertainty_pct is not null order by created_at,id", (user,)).fetchall()
            observations = []
            for item in history:
                vals = {k: float(item[k]) if item[k] is not None else None for k in tfsf.DIMENSIONS}
                variances = {k: max(abs(v) * float(item["uncertainty_pct"]) / 100, 0.01) ** 2 for k, v in vals.items() if v is not None}
                observations.append(tfsf.Observation(item["created_at"].isoformat(), vals, variances, "INR"))
            state = tfsf.fuse(observations, tfsf.Config(prior_variance=1e30))[-1]
            estimate = {"mean": dict(zip(tfsf.DIMENSIONS, state.mean)), "variance": dict(zip(tfsf.DIMENSIONS, state.variance)),
                        "initialized": list(state.initialized), "changes": list(state.changes),
                        "model_version": "tfsf-diagonal-v1", "assumption": "User-supplied measurement uncertainty and random-walk model; not a coverage guarantee."}
        return {"profile": profile_payload(row), "model_version": "self-reported-arithmetic-v1", "tfsf": estimate}


@app.post("/v1/profile")
def save_profile(payload: ProfileInput, user: User):
    with connection(user) as conn:
        require_consent(conn, user)
        row = conn.execute("""insert into investiq_app.financial_snapshots(owner_id,income,expenses,assets,liabilities,goal,uncertainty_pct,currency)
            values(%s,%s,%s,%s,%s,%s,%s,'INR') returning *""", (user,payload.income,payload.expenses,payload.assets,payload.liabilities,payload.goal,payload.uncertainty_pct)).fetchone()
        audit(conn, user, "financial_snapshot_created", row["id"])
        return {"profile": profile_payload(row)}


class ScenarioInput(BaseModel):
    income_change: Decimal = Field(ge=-1_000_000_000_000, le=1_000_000_000_000)
    expense_change: Decimal = Field(ge=-1_000_000_000_000, le=1_000_000_000_000)


@app.post("/v1/scenarios/budget")
def scenario(payload: ScenarioInput, user: User):
    with connection(user) as conn:
        require_consent(conn, user)
        row = conn.execute("select * from investiq_app.financial_snapshots where owner_id=%s order by created_at desc,id desc limit 1", (user,)).fetchone()
        if not row:
            raise HTTPException(409, "Add a financial snapshot before running a scenario.")
        if row["income"] + payload.income_change < 0 or row["expenses"] + payload.expense_change < 0:
            raise HTTPException(422, "Scenario income and expenses cannot be negative.")
        base = row["income"] - row["expenses"]
        # ECCR executes an explicit accounting identity, not a causal market claim.
        now = datetime.now(timezone.utc).isoformat()
        source = eccr.Evidence("snapshot", "user-self-reported-snapshot", row["created_at"].isoformat())
        nodes = (eccr.Node("income", "INR/month", float(row["income"]), 0, assumption_evidence=("snapshot",), allowed_range=(0, 2e12)),
                 eccr.Node("expenses", "INR/month", float(row["expenses"]), 0, assumption_evidence=("snapshot",), allowed_range=(0, 2e12)),
                 eccr.Node("surplus", "INR/month", 0, 0, {"income":1., "expenses":-1.}, ("snapshot",)))
        model = eccr.Scenario(now, nodes, (source,), (), {"income":eccr.Intervention(float(row["income"]+payload.income_change), "INR/month"),
                        "expenses":eccr.Intervention(float(row["expenses"]+payload.expense_change), "INR/month")}, True, True)
        simulated = eccr.simulate(model, eccr.Config(draws=1, seed=0))
        if simulated["status"] != "OK": raise HTTPException(422, "Scenario is not admissible.")
        alternative = base + payload.income_change - payload.expense_change
        audit(conn, user, "budget_scenario_calculated", row["id"], {"model_version":"eccr-budget-identity-v1"})
        return {"snapshot_id": str(row["id"]), "base_surplus": str(base), "scenario_surplus": str(alternative),
                "difference": str(alternative-base), "currency":"INR", "model_version":"eccr-budget-identity-v1",
                "explanation":"Scenario surplus = (income + income change) − (expenses + expense change).",
                "uncertainty":"A hypothetical accounting comparison, not a causal forecast or probability distribution."}


@app.get("/v1/overview")
def overview(user: User):
    with connection(user) as conn:
        sources = conn.execute("select count(*) as n from investiq_evidence.sources where owner_id=%s", (user,)).fetchone()["n"]
        claims = conn.execute("select count(*) as n from investiq_evidence.claims where owner_id=%s and verification_status='human_verified'", (user,)).fetchone()["n"]
        forecasts = conn.execute("select count(*) as n from investiq_app.forecasts where owner_id=%s", (user,)).fetchone()["n"]
        last = conn.execute("select max(retrieved_at) as latest from investiq_evidence.documents where owner_id=%s and available_at<=now() and retrieved_at<=now()", (user,)).fetchone()["latest"]
        return {"sources":sources, "verified_claims":claims, "forecasts":forecasts,
                "last_retrieved_at":last.isoformat() if last else None, "consent":consent_granted(conn,user)}


@app.get("/v1/market/{kind}")
def market(kind: str, user: User):
    if kind not in ("stocks", "funds"):
        raise HTTPException(404, "Unknown market collection.")
    document_type = "market_prices" if kind == "stocks" else "fund_nav"
    metric = "close" if kind == "stocks" else "nav"
    with connection(user) as conn:
        rows = conn.execute("""with ranked as (select o.*,row_number() over(partition by o.logical_key order by o.available_at desc,o.retrieved_at desc,o.id desc) as rank
          from investiq_evidence.observations o where o.owner_id=%s and o.available_at<=now() and o.retrieved_at<=now())
          select r.entity_id,r.original_value,r.currency,r.unit,r.period_end,r.published_at,r.available_at,r.retrieved_at,r.version_identifier,
                 d.id as document_id,d.original_source_uri,s.provider
          from ranked r join investiq_evidence.documents d on d.owner_id=r.owner_id and d.id=r.document_id
          join investiq_evidence.sources s on s.owner_id=r.owner_id and s.id=r.source_id
          where r.rank=1 and r.validation_status='valid' and r.metric=%s and r.currency='INR' and d.document_type=%s
          order by r.period_end desc, r.entity_id limit 100""", (user,metric,document_type)).fetchall()
        return {"items":rows, "note":"As-known imported records; not a live feed. Verify identifiers, licensing and corporate actions."}


@app.get("/v1/evidence")
def evidence(user: User):
    with connection(user) as conn:
        docs = conn.execute("""select d.id,d.document_type,d.original_source_uri,d.published_at,d.available_at,d.retrieved_at,d.version_identifier,d.content_sha256,s.provider,s.license
          from investiq_evidence.documents d join investiq_evidence.sources s on s.owner_id=d.owner_id and s.id=d.source_id
          where d.owner_id=%s and d.available_at<=now() and d.retrieved_at<=now() order by d.retrieved_at desc limit 100""", (user,)).fetchall()
        return {"documents": docs}


@app.get("/v1/evidence/{document_id}")
def evidence_detail(document_id: UUID, user: User):
    with connection(user) as conn:
        doc = conn.execute("""select d.id,d.document_type,d.original_source_uri,d.published_at,d.available_at,d.retrieved_at,d.version_identifier,d.content_sha256,s.provider,s.license
          from investiq_evidence.documents d join investiq_evidence.sources s on s.owner_id=d.owner_id and s.id=d.source_id
          where d.owner_id=%s and d.id=%s and d.available_at<=now() and d.retrieved_at<=now()""", (user,document_id)).fetchone()
        if not doc: raise HTTPException(404, "Evidence not found.")
        claims = conn.execute("select id,quote,verification_status,extractor_version from investiq_evidence.claims where owner_id=%s and document_id=%s and created_at<=now() order by char_start",(user,document_id)).fetchall()
        return {"document": doc, "claims": claims}


@app.get("/v1/research")
def research(user: User):
    with connection(user) as conn:
        rows = conn.execute("""select c.id,c.quote,c.verification_status,c.document_id,d.original_source_uri,s.provider,d.available_at
          from investiq_evidence.claims c join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
          join investiq_evidence.sources s on s.owner_id=d.owner_id and s.id=d.source_id
          where c.owner_id=%s and c.verification_status='human_verified' and c.created_at<=now() and d.available_at<=now() and d.retrieved_at<=now()
          order by c.created_at desc limit 100""", (user,)).fetchall()
        relations = conn.execute("""select r.from_claim_id,r.to_claim_id,r.relation,r.rationale from investiq_evidence.claim_relations r
          join investiq_evidence.claims a on a.owner_id=r.owner_id and a.id=r.from_claim_id
          join investiq_evidence.claims b on b.owner_id=r.owner_id and b.id=r.to_claim_id
          join investiq_evidence.documents da on da.owner_id=a.owner_id and da.id=a.document_id
          join investiq_evidence.documents db on db.owner_id=b.owner_id and db.id=b.document_id
          where r.owner_id=%s and a.verification_status='human_verified' and b.verification_status='human_verified'
          and da.available_at<=now() and da.retrieved_at<=now() and db.available_at<=now() and db.retrieved_at<=now() order by r.created_at desc limit 100""", (user,)).fetchall()
        return {"claims":rows,"relations":relations,"note":"Verification denotes a recorded human review, not proof of truth."}


class ForecastInput(BaseModel):
    event: str = Field(min_length=1, max_length=300)
    probability: Decimal = Field(ge=0, le=1)
    target_at: datetime
    evidence_claim_id: UUID
    model_version: str = Field(default="user-entered-v1", min_length=1, max_length=100)


@app.get("/v1/forecasts")
def forecasts(user: User):
    with connection(user) as conn:
        rows = conn.execute("""select f.id,f.event,f.probability,f.target_at,f.issued_at,f.model_version,f.evidence_claim_id,c.document_id,
          r.outcome,r.resolved_at, case when r.outcome is not null then power(f.probability - (case when r.outcome then 1 else 0 end),2) end as brier
          from investiq_app.forecasts f join investiq_evidence.claims c on c.owner_id=f.owner_id and c.id=f.evidence_claim_id
          left join investiq_app.forecast_resolutions r on r.owner_id=f.owner_id and r.forecast_id=f.id
          where f.owner_id=%s order by f.issued_at desc limit 100""", (user,)).fetchall()
        return {"forecasts": rows, "note":"User-entered predictions; resolution is a user assertion linked to reviewed evidence."}


@app.post("/v1/forecasts")
def add_forecast(payload: ForecastInput, user: User):
    if payload.target_at.tzinfo is None or payload.target_at <= datetime.now(timezone.utc):
        raise HTTPException(422, "Forecast target must be a timezone-aware future time.")
    with connection(user) as conn:
        claim = conn.execute("""select c.id from investiq_evidence.claims c join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
            where c.owner_id=%s and c.id=%s and c.verification_status='human_verified' and d.available_at<=now() and d.retrieved_at<=now()""", (user,payload.evidence_claim_id)).fetchone()
        if not claim: raise HTTPException(422, "A human-reviewed, available evidence claim is required.")
        row = conn.execute("insert into investiq_app.forecasts(owner_id,event,probability,target_at,model_version,evidence_claim_id) values(%s,%s,%s,%s,%s,%s) returning id,issued_at", (user,payload.event,payload.probability,payload.target_at,payload.model_version,payload.evidence_claim_id)).fetchone()
        audit(conn,user,"forecast_issued",row["id"],{"model_version":payload.model_version})
        return row


class ResolutionInput(BaseModel):
    outcome: bool
    evidence_claim_id: UUID


@app.post("/v1/forecasts/{forecast_id}/resolve")
def resolve_forecast(forecast_id: UUID, payload: ResolutionInput, user: User):
    with connection(user) as conn:
        f = conn.execute("select * from investiq_app.forecasts where owner_id=%s and id=%s", (user,forecast_id)).fetchone()
        if not f: raise HTTPException(404,"Forecast not found.")
        if f["target_at"] > datetime.now(timezone.utc): raise HTTPException(409,"Forecast has not matured.")
        claim = conn.execute("""select c.id from investiq_evidence.claims c join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
            where c.owner_id=%s and c.id=%s and c.verification_status='human_verified' and d.available_at> %s and d.available_at<=now() and d.retrieved_at<=now()""", (user,payload.evidence_claim_id,f["issued_at"])).fetchone()
        if not claim: raise HTTPException(422,"Resolution requires later human-reviewed evidence.")
        row = conn.execute("insert into investiq_app.forecast_resolutions(owner_id,forecast_id,evidence_claim_id,outcome) values(%s,%s,%s,%s) returning id,resolved_at", (user,forecast_id,payload.evidence_claim_id,payload.outcome)).fetchone()
        audit(conn,user,"forecast_resolved",forecast_id)
        return row


class AskInput(BaseModel):
    question: str = Field(min_length=2, max_length=300)


@app.post("/v1/assistant")
def assistant(payload: AskInput, user: User):
    # Retrieval only, never fabricate a model-generated conclusion.
    tokens = [w for w in payload.question.split() if len(w) >= 3][:5]
    with connection(user) as conn:
        rows = []
        if tokens:
            rows = conn.execute("""select c.quote,c.id,c.document_id,d.original_source_uri,s.provider from investiq_evidence.claims c
              join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
              join investiq_evidence.sources s on s.owner_id=d.owner_id and s.id=d.source_id
              where c.owner_id=%s and c.verification_status='human_verified' and d.available_at<=now() and d.retrieved_at<=now()
                and c.quote ilike any(%s) order by c.created_at desc limit 5""", (user,["%" + w.replace("%", "").replace("_", "") + "%" for w in tokens])).fetchall()
        audit(conn,user,"evidence_assistant_search",details={"match_count":len(rows), "mode":"retrieval-only-v1"})
        return {"mode":"retrieval-only-v1", "matches":rows,
                "message":"Reviewed evidence found. Open the original sources before drawing a conclusion." if rows else "No reviewed evidence matched. No conclusion has been generated."}


@app.post("/v1/reports")
def report(user: User):
    with connection(user) as conn:
        rows = conn.execute("""select c.id,c.quote,c.document_id,d.original_source_uri,s.provider from investiq_evidence.claims c
          join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
          join investiq_evidence.sources s on s.owner_id=d.owner_id and s.id=d.source_id
          where c.owner_id=%s and c.verification_status='human_verified' and d.available_at<=now() and d.retrieved_at<=now()
          order by c.created_at desc limit 20""",(user,)).fetchall()
        if not rows: raise HTTPException(409,"No reviewed evidence is available for a report.")
        audit(conn,user,"evidence_report_generated",details={"claim_count":len(rows),"model_version":"evidence-digest-v1"})
        return {"title":"Evidence digest", "model_version":"evidence-digest-v1", "claims":rows,
                "disclaimer":"A source-linked digest, not an investment conclusion. Human verification is not proof of truth."}


@app.get("/v1/profile/history")
def profile_history(user: User):
    with connection(user) as conn:
        require_consent(conn,user)
        rows = conn.execute("select * from investiq_app.financial_snapshots where owner_id=%s order by created_at desc,id desc limit 50", (user,)).fetchall()
        return {"snapshots":[profile_payload(r) for r in rows]}


@app.get("/v1/audit")
def audit_history(user: User):
    with connection(user) as conn:
        rows = conn.execute("select id,action,subject_id,details,created_at from investiq_app.audit_events where owner_id=%s order by created_at desc,id desc limit 100",(user,)).fetchall()
        return {"events": rows, "note":"Append-only application activity; not an independently tamper-proof audit log."}

# Phase 2 planning endpoints: amounts are returned as decimal strings, never binary floats.
from datetime import date
from investiq.platform import planning

Money = Annotated[Decimal, Field(ge=0, le=1_000_000_000_000, decimal_places=2)]

class MissionInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    start_date: date
    months: int = Field(ge=1, le=600)
    target: Decimal = Field(gt=0, le=1_000_000_000_000, decimal_places=2)
    monthly_contribution: Money
    monthly_income: Money
    personal_expenses: Money
    learning_expenses: Money
    emergency_expenses: Money
    benefits_confirmed: bool = False

class ContributionInput(BaseModel):
    mission_id: UUID
    amount: Decimal = Field(gt=0, le=1_000_000_000_000, decimal_places=2)
    contribution_date: date

class SavingsCalculator(BaseModel):
    monthly_contribution: Money
    months: int = Field(ge=0, le=600)

class InterestCalculator(BaseModel):
    principal: Money
    annual_rate_pct: Decimal = Field(ge=0, le=100, decimal_places=8, max_digits=11)
    months: int = Field(ge=0, le=600)

class SipCalculator(BaseModel):
    monthly_contribution: Money
    annual_rate_pct: Decimal = Field(ge=0, le=100, decimal_places=8, max_digits=11)
    months: int = Field(ge=0, le=600)

def plan_payload(row):
    return {k: planning.money(v) if isinstance(v, Decimal) else str(v) if isinstance(v, UUID) else v.isoformat() if isinstance(v, (date, datetime)) else v for k,v in row.items()}

@app.post('/v1/goals')
def create_mission(payload: MissionInput, user: User):
    if not payload.title.strip(): raise HTTPException(422, 'Title cannot be blank.')
    with connection(user) as conn:
        require_consent(conn, user)
        row = conn.execute('''insert into investiq_app.savings_missions(owner_id,title,start_date,months,target,monthly_contribution,monthly_income,personal_expenses,learning_expenses,emergency_expenses,benefits_confirmed)
          values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning *''', (user,payload.title.strip(),payload.start_date,payload.months,payload.target,payload.monthly_contribution,payload.monthly_income,payload.personal_expenses,payload.learning_expenses,payload.emergency_expenses,payload.benefits_confirmed)).fetchone()
        audit(conn, user, 'savings_mission_created', row['id'])
        return {'mission': plan_payload(row)}

@app.get('/v1/goals')
def list_missions(user: User):
    with connection(user) as conn:
        require_consent(conn, user)
        rows = conn.execute('select * from investiq_app.savings_missions where owner_id=%s order by created_at desc,id desc limit 100', (user,)).fetchall()
        return {'missions': [plan_payload(r) for r in rows]}

@app.post('/v1/savings/contributions')
def add_contribution(payload: ContributionInput, user: User):
    with connection(user) as conn:
        require_consent(conn, user)
        mission = conn.execute('select * from investiq_app.savings_missions where owner_id=%s and id=%s', (user,payload.mission_id)).fetchone()
        if not mission: raise HTTPException(404, 'Mission not found.')
        if payload.contribution_date < mission['start_date'] or payload.contribution_date > datetime.now(timezone.utc).date():
            raise HTTPException(422, 'Contribution date must be on or after mission start and no later than today (UTC).')
        row = conn.execute('insert into investiq_app.savings_contributions(owner_id,mission_id,amount,contribution_date) values(%s,%s,%s,%s) returning *', (user,payload.mission_id,payload.amount,payload.contribution_date)).fetchone()
        audit(conn,user,'savings_contribution_recorded',row['id'],{'mission_id':str(payload.mission_id)})
        return {'contribution':plan_payload(row)}

@app.get('/v1/goals/progress')
def mission_progress(user: User):
    with connection(user) as conn:
        require_consent(conn,user)
        mission = conn.execute('select * from investiq_app.savings_missions where owner_id=%s order by created_at desc,id desc limit 1',(user,)).fetchone()
        if not mission: return {'mission':None,'contributions':[],'progress':None}
        rows = conn.execute('select * from investiq_app.savings_contributions where owner_id=%s and mission_id=%s order by contribution_date desc,created_at desc,id desc',(user,mission['id'])).fetchall()
        return {'mission':plan_payload(mission),'contributions':[plan_payload(r) for r in rows], 'progress':planning.mission_progress(mission, rows, datetime.now(timezone.utc).date())}

@app.post('/v1/calculators/savings')
def calculate_savings(payload: SavingsCalculator, user: User):
    return {**planning.savings(payload.monthly_contribution, payload.months), 'calculation_date':datetime.now(timezone.utc).date().isoformat()}

@app.post('/v1/calculators/compound-interest')
def calculate_compound(payload: InterestCalculator, user: User):
    return {**planning.compound(payload.principal,payload.annual_rate_pct,payload.months), 'calculation_date':datetime.now(timezone.utc).date().isoformat()}

@app.post('/v1/calculators/sip')
def calculate_sip(payload: SipCalculator, user: User):
    return {**planning.sip(payload.monthly_contribution,payload.annual_rate_pct,payload.months), 'calculation_date':datetime.now(timezone.utc).date().isoformat()}
