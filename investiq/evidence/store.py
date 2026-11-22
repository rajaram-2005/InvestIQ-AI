"""Trusted-server PostgreSQL operations. Never expose this API using service credentials to clients."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import uuid
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .core import Document, Source, EXTRACTOR_VERSION, extract_announcements, extract_claims, extract_observations, extract_text, stable_hash, timestamp


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def ingest(conn: psycopg.Connection, owner_id: str, source: Source, document: Document, raw: bytes) -> dict:
    """All writes in one transaction. Caller must independently authorize owner_id."""
    uuid.UUID(owner_id); uuid.UUID(source.id); uuid.UUID(document.id)
    source.validate(); document.validate()
    observations = extract_observations(raw, source, document)
    is_narrative = document.document_type in {"financial_document", "corporate_announcement"}
    # Extracted text is equivalent to raw content for plain text; never persist it
    # or quote fragments unless raw storage is explicitly permitted.
    text = extract_text(raw, document) if is_narrative and source.allow_raw_storage else None
    claims = extract_claims(text) if text is not None else []
    announcements = extract_announcements(text) if text is not None and document.document_type == "corporate_announcement" else []
    sha = hashlib.sha256(raw).hexdigest()
    with conn.transaction():
        conn.execute("""insert into investiq_evidence.sources
            (id, owner_id, provider, source_uri, license, license_reference, allow_raw_storage,
             allow_derived_storage, allow_redistribution, version_identifier)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict (id) do nothing""",
            (source.id, owner_id, source.provider, source.source_uri, source.license, source.license_reference,
             source.allow_raw_storage, source.allow_derived_storage, source.allow_redistribution, source.version_identifier))
        # Reject stale or cross-tenant source reuse. Do not silently revise license metadata.
        db_source = conn.execute("""select provider, source_uri, license, license_reference, allow_raw_storage,
                allow_derived_storage, allow_redistribution, version_identifier from investiq_evidence.sources
                where owner_id=%s and id=%s""", (owner_id, source.id)).fetchone()
        expected = (source.provider, source.source_uri, source.license, source.license_reference,
                    source.allow_raw_storage, source.allow_derived_storage, source.allow_redistribution, source.version_identifier)
        if db_source is None or tuple(db_source) != expected:
            raise ValueError("source ID already exists with different owner or metadata")
        conn.execute("""insert into investiq_evidence.documents
             (id,owner_id,source_id,original_source_uri,document_type,media_type,content_sha256,raw_content,
              extracted_text,extractor_version,published_at,available_at,version_identifier)
             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
             (document.id, owner_id, source.id, document.original_source_uri, document.document_type,
              document.media_type, sha, raw if source.allow_raw_storage else None, text, EXTRACTOR_VERSION,
              timestamp(document.published_at), timestamp(document.available_at), document.version_identifier))
        ids = []
        for obs in observations:
            oid = str(uuid.uuid4()); ids.append(oid)
            conn.execute("""insert into investiq_evidence.observations
                (id,owner_id,source_id,document_id,logical_key,entity_id,metric,original_value,numeric_value,
                 currency,unit,period_start,period_end,published_at,available_at,validation_status,validation_errors,
                 version_identifier,supersedes_id) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (oid, owner_id, source.id, document.id, obs.logical_key, obs.entity_id, obs.metric, obs.original_value,
                 Decimal(obs.numeric_value), obs.currency, obs.unit, obs.period_start, obs.period_end,
                 timestamp(obs.published_at), timestamp(obs.available_at), obs.validation_status, Jsonb(list(obs.validation_errors)),
                 obs.version_identifier, obs.supersedes_id))
        claim_ids = []
        for claim in claims:
            cid = str(uuid.uuid4()); claim_ids.append(cid)
            conn.execute("""insert into investiq_evidence.claims
                (id,owner_id,document_id,quote,char_start,char_end,extractor_version,verification_status)
                values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (cid, owner_id, document.id, claim.quote, claim.char_start, claim.char_end, claim.extractor_version, claim.verification_status))
        announcement_ids = []
        for event in announcements:
            eid = str(uuid.uuid4()); announcement_ids.append(eid)
            conn.execute("""insert into investiq_evidence.announcements
                (id,owner_id,document_id,event_type,quote,char_start,char_end,extractor_version,verification_status)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (eid, owner_id, document.id, event["event_type"], event["quote"], event["char_start"], event["char_end"], event["extractor_version"], event["verification_status"]))
    return {"document_id": document.id, "content_sha256": sha, "observation_ids": ids, "announcement_ids": announcement_ids,
            "claim_ids": claim_ids, "quarantined": sum(o.validation_status == "quarantined" for o in observations),
            "warnings": ["narrative text and claims not stored: raw storage not permitted"] if is_narrative and text is None else []}


def link_claims(conn: psycopg.Connection, owner_id: str, from_id: str, to_id: str, relation: str, rationale: str) -> str:
    """Human/external annotations only; same-owner foreign keys enforced by DB."""
    if relation not in {"supports", "contradicts", "references"} or not rationale.strip() or from_id == to_id:
        raise ValueError("invalid relation")
    identifier = str(uuid.uuid4())
    with conn.transaction():
        conn.execute("""insert into investiq_evidence.claim_relations (id,owner_id,from_claim_id,to_claim_id,relation,rationale)
                        values (%s,%s,%s,%s,%s,%s)""", (identifier, owner_id, from_id, to_id, relation, rationale))
    return identifier


# Rank all latest versions before filtering quality: a bad correction must not silently resurrect an older value.
AS_OF_SQL = """with ranked as (
    select o.*, row_number() over (partition by o.logical_key
        order by o.available_at desc, o.retrieved_at desc, o.id desc) as rank
    from investiq_evidence.observations o
    where o.owner_id = %s and o.available_at <= %s and o.retrieved_at <= %s
)
select r.id, r.logical_key, r.entity_id, r.metric, r.original_value, r.numeric_value, r.currency, r.unit,
       r.period_start, r.period_end, r.published_at, r.available_at, r.retrieved_at,
       r.validation_status, r.validation_errors, r.version_identifier, r.supersedes_id,
       d.id as document_id, d.original_source_uri, d.content_sha256, d.document_type,
       d.published_at as document_published_at, d.available_at as document_available_at,
       d.retrieved_at as document_retrieved_at, d.version_identifier as document_version,
       s.id as source_id, s.provider, s.source_uri, s.license, s.license_reference,
       s.allow_raw_storage, s.allow_derived_storage, s.allow_redistribution, s.version_identifier as source_version
from ranked r join investiq_evidence.documents d on d.owner_id=r.owner_id and d.id=r.document_id
join investiq_evidence.sources s on s.owner_id=r.owner_id and s.id=r.source_id
where r.rank=1 order by r.logical_key"""


def create_snapshot(conn: psycopg.Connection, owner_id: str, as_of: str) -> dict:
    """Freeze a consistent as-known information set and quality exclusions in one row."""
    uuid.UUID(owner_id)
    cutoff = timestamp(as_of)
    # Isolation must be the first statement inside this transaction; use a fresh connection.
    if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
        raise ValueError("create_snapshot requires an idle connection")
    with conn.transaction():
        conn.execute("set transaction isolation level repeatable read")
        now = conn.execute("select clock_timestamp()").fetchone()[0]
        if cutoff > now:
            raise ValueError("future cutoff is not allowed")
        with conn.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(AS_OF_SQL, (owner_id, cutoff, cutoff)).fetchall()
        normalized = [_json_value(row) for row in rows]
        # Freeze document/claim graph metadata under the same database snapshot.
        with conn.cursor(row_factory=dict_row) as cursor:
            docs = cursor.execute("""select d.id, d.source_id, d.original_source_uri, d.document_type, d.media_type,
                  d.content_sha256, (d.raw_content is not null) as raw_stored, d.extractor_version,
                  d.published_at, d.available_at, d.retrieved_at, d.version_identifier,
                  s.provider, s.source_uri, s.license, s.license_reference, s.allow_raw_storage,
                  s.allow_derived_storage, s.allow_redistribution, s.version_identifier as source_version
                  from investiq_evidence.documents d join investiq_evidence.sources s
                  on s.owner_id=d.owner_id and s.id=d.source_id
                  where d.owner_id=%s and d.available_at<=%s and d.retrieved_at<=%s order by d.id""",
                  (owner_id, cutoff, cutoff)).fetchall()
            claims = cursor.execute("""select c.id,c.document_id,c.quote,c.char_start,c.char_end,c.extractor_version,c.verification_status,c.created_at
                  from investiq_evidence.claims c join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
                  where c.owner_id=%s and c.created_at<=%s and d.available_at<=%s and d.retrieved_at<=%s order by c.id""",
                  (owner_id, cutoff, cutoff, cutoff)).fetchall()
            announcements = cursor.execute("""select a.id,a.document_id,a.event_type,a.quote,a.char_start,a.char_end,a.extractor_version,a.verification_status,a.created_at
                  from investiq_evidence.announcements a join investiq_evidence.documents d on d.owner_id=a.owner_id and d.id=a.document_id
                  where a.owner_id=%s and a.created_at<=%s and d.available_at<=%s and d.retrieved_at<=%s order by a.id""",
                  (owner_id, cutoff, cutoff, cutoff)).fetchall()
            relations = cursor.execute("""select r.id,r.from_claim_id,r.to_claim_id,r.relation,r.rationale,r.created_at
                  from investiq_evidence.claim_relations r
                  join investiq_evidence.claims a on a.owner_id=r.owner_id and a.id=r.from_claim_id
                  join investiq_evidence.claims b on b.owner_id=r.owner_id and b.id=r.to_claim_id
                  join investiq_evidence.documents da on da.owner_id=a.owner_id and da.id=a.document_id
                  join investiq_evidence.documents db on db.owner_id=b.owner_id and db.id=b.document_id
                  where r.owner_id=%s and r.created_at<=%s and a.created_at<=%s and b.created_at<=%s
                    and da.available_at<=%s and da.retrieved_at<=%s and db.available_at<=%s and db.retrieved_at<=%s order by r.id""",
                  (owner_id, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff, cutoff)).fetchall()
            embeddings = cursor.execute("""select e.id,e.claim_id,e.model_identifier,e.embedding::text as embedding,e.created_at
                  from investiq_evidence.claim_embeddings e join investiq_evidence.claims c on c.owner_id=e.owner_id and c.id=e.claim_id
                  join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
                  where e.owner_id=%s and e.created_at<=%s and c.created_at<=%s and d.available_at<=%s and d.retrieved_at<=%s order by e.id""",
                  (owner_id, cutoff, cutoff, cutoff, cutoff)).fetchall()
        manifest = {"schema": "investiq-pit-v1", "as_of": cutoff.isoformat(), "owner_id": owner_id,
                    "selection": "latest-known-per-logical-key-then-quality-filter",
                    "observations": [r for r in normalized if r["validation_status"] == "valid"],
                    "excluded_latest": [r for r in normalized if r["validation_status"] != "valid"],
                    "documents": _json_value(docs), "claims": _json_value(claims),
                    "announcements": _json_value(announcements), "relations": _json_value(relations),
                    "embeddings": _json_value(embeddings)}
        digest = stable_hash(manifest)
        snapshot_id = str(uuid.uuid4())
        conn.execute("""insert into investiq_evidence.snapshots(id,owner_id,as_of,manifest_version,manifest,manifest_sha256)
                        values (%s,%s,%s,%s,%s,%s)""",
                     (snapshot_id, owner_id, cutoff, "investiq-pit-v1", Jsonb(manifest), digest))
    return {"snapshot_id": snapshot_id, "manifest_sha256": digest, "manifest": manifest}


def provenance_report(manifest: dict) -> dict:
    rows = manifest["observations"]
    providers: dict[str, int] = {}
    for item in rows:
        providers[item["provider"]] = providers.get(item["provider"], 0) + 1
    docs = manifest.get("documents", [])
    licenses = sorted({(d["provider"], d["license"], d["license_reference"], d["allow_redistribution"]) for d in docs})
    return {"as_of": manifest["as_of"], "manifest_sha256": stable_hash(manifest), "valid_count": len(rows),
            "quarantined_latest_count": len(manifest["excluded_latest"]), "providers": providers,
            "document_count": len(docs), "claim_count": len(manifest.get("claims", [])),
            "announcement_count": len(manifest.get("announcements", [])), "relationship_count": len(manifest.get("relations", [])),
            "embedding_count": len(manifest.get("embeddings", [])),
            "licenses": [{"provider": p, "license": l, "reference": ref, "redistribution_allowed": allowed} for p, l, ref, allowed in licenses],
            "restricted_observations": sum(not row["allow_redistribution"] for row in rows),
            "note": "Point-in-time counts; source licenses may also restrict redistribution of summaries."}


def get_snapshot(conn: psycopg.Connection, owner_id: str, snapshot_id: str) -> dict:
    """Retrieve and verify a private snapshot; client authentication is caller's responsibility."""
    uuid.UUID(owner_id); uuid.UUID(snapshot_id)
    row = conn.execute("select manifest,manifest_sha256 from investiq_evidence.snapshots where owner_id=%s and id=%s",
                       (owner_id, snapshot_id)).fetchone()
    if row is None:
        raise LookupError("snapshot not found")
    manifest, expected = row
    if stable_hash(manifest) != expected:
        raise ValueError("snapshot manifest hash mismatch")
    return manifest


def add_embedding(conn: psycopg.Connection, owner_id: str, claim_id: str, model: str, values: list[float]) -> str:
    """Store an externally produced 384-dimension embedding; no external calls are made."""
    import math
    if (not model.strip() or len(values) != 384 or not all(math.isfinite(float(v)) and abs(float(v)) <= 3e38 for v in values)
            or not any(float(v) != 0 for v in values)):
        raise ValueError("model and 384 finite, nonzero float32-compatible coordinates required")
    encoded = "[" + ",".join(str(float(v)) for v in values) + "]"
    identifier = str(uuid.uuid4())
    with conn.transaction():
        conn.execute("""insert into investiq_evidence.claim_embeddings(id,owner_id,claim_id,model_identifier,embedding)
                        values (%s,%s,%s,%s,%s::extensions.vector)""", (identifier, owner_id, claim_id, model, encoded))
    return identifier


def search_claims(conn: psycopg.Connection, owner_id: str, model: str, values: list[float], as_of: str, limit: int = 10) -> list[dict]:
    """Brute-force pgvector cosine search over owner-scoped, as-known claims.

    Similarity is not verification or evidence of entailment. Caller must authorize owner_id.
    """
    import math
    uuid.UUID(owner_id)
    if (not model.strip() or len(values) != 384 or not all(math.isfinite(float(v)) and abs(float(v)) <= 3e38 for v in values)
            or not any(float(v) != 0 for v in values) or not 1 <= limit <= 100):
        raise ValueError("invalid model, query vector or limit")
    cutoff = timestamp(as_of)
    encoded = "[" + ",".join(str(float(v)) for v in values) + "]"
    with conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute("""select c.id,c.quote,c.verification_status,c.document_id,d.original_source_uri,
              d.content_sha256,e.model_identifier,
              e.embedding OPERATOR(extensions.<=>) %s::extensions.vector as cosine_distance
              from investiq_evidence.claim_embeddings e
              join investiq_evidence.claims c on c.owner_id=e.owner_id and c.id=e.claim_id
              join investiq_evidence.documents d on d.owner_id=c.owner_id and d.id=c.document_id
              where e.owner_id=%s and e.model_identifier=%s and e.created_at<=%s and c.created_at<=%s
                and d.retrieved_at<=%s and d.available_at<=%s
              order by cosine_distance, c.id limit %s""",
              (encoded, owner_id, model, cutoff, cutoff, cutoff, cutoff, limit)).fetchall()
    return [_json_value(row) for row in rows]
