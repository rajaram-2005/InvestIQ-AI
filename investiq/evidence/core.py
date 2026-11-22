"""Pure extraction and quality rules. No network requests or credential handling."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import hashlib
import io
import json
import re
from typing import Any
from urllib.parse import urlparse

import networkx as nx
from pypdf import PdfReader

EXTRACTOR_VERSION = "investiq-evidence-1"
MAX_BYTES = 10_000_000


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Source:
    id: str
    provider: str
    source_uri: str
    license: str
    license_reference: str
    allow_raw_storage: bool
    allow_derived_storage: bool
    allow_redistribution: bool
    version_identifier: str

    def validate(self):
        if not all((self.id, self.provider.strip(), self.license.strip(), self.license_reference.strip(), self.version_identifier.strip())):
            raise ValueError("source identity, license and version required")
        parsed = urlparse(self.source_uri)
        if parsed.scheme not in ("https", "synthetic") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("source URI must be HTTPS or synthetic, without credentials, query or fragment")
        if not self.allow_derived_storage:
            raise ValueError("source does not permit derived storage")
        if not all(isinstance(x, bool) for x in (self.allow_raw_storage, self.allow_derived_storage, self.allow_redistribution)):
            raise ValueError("license permissions must be booleans")


@dataclass(frozen=True)
class Document:
    id: str
    document_type: str
    original_source_uri: str
    media_type: str
    published_at: str
    available_at: str
    version_identifier: str

    def validate(self):
        parsed = urlparse(self.original_source_uri)
        if parsed.scheme not in ("https", "synthetic") or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("invalid original source URI")
        if self.document_type not in ("financial_document", "market_prices", "fund_nav", "financial_statement", "corporate_announcement"):
            raise ValueError("unsupported document type")
        allowed = {"financial_document": {"text/plain", "application/pdf"}, "corporate_announcement": {"text/plain", "application/pdf"},
                   "market_prices": {"text/csv"}, "fund_nav": {"text/csv"}, "financial_statement": {"application/json"}}
        if self.media_type not in allowed[self.document_type] or not self.id or not self.version_identifier.strip():
            raise ValueError("invalid document metadata or media type")
        if timestamp(self.available_at) < timestamp(self.published_at):
            raise ValueError("availability precedes publication")


@dataclass(frozen=True)
class Observation:
    logical_key: str
    entity_id: str
    metric: str
    original_value: str
    numeric_value: str
    currency: str
    unit: str
    period_start: str
    period_end: str
    published_at: str
    available_at: str
    validation_status: str
    validation_errors: tuple[str, ...]
    version_identifier: str
    supersedes_id: str | None = None


@dataclass(frozen=True)
class Claim:
    quote: str
    char_start: int
    char_end: int
    extractor_version: str = EXTRACTOR_VERSION
    verification_status: str = "unverified"


def _observation(row: dict, source_id: str, document: Document, metric: str, raw: Any, unit: str) -> Observation:
    entity = str(row.get("entity_id", "")).strip()
    if not entity or not metric or not unit:
        raise ValueError("entity, metric and unit required")
    currency = str(row.get("currency", ""))
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency must be an uppercase three-letter code; use XXX for no currency")
    start, end = date.fromisoformat(str(row["period_start"])), date.fromisoformat(str(row["period_end"]))
    if end < start:
        raise ValueError("period end precedes start")
    published, available = timestamp(str(row["published_at"])), timestamp(str(row["available_at"]))
    if available < published or published < timestamp(document.published_at) or available < timestamp(document.available_at):
        raise ValueError("invalid or pre-document observation timing")
    original = str(raw)
    if not original.strip():
        raise ValueError("numeric original value required")
    try:
        numeric = Decimal(original.strip())
    except InvalidOperation as exc:
        raise ValueError("numeric original value required") from exc
    if not numeric.is_finite():
        raise ValueError("nonfinite numeric value")
    version = str(row.get("version_identifier", "")).strip()
    if not version:
        raise ValueError("observation version_identifier required")
    errors = []
    if metric in {"open", "high", "low", "close", "nav"} and numeric <= 0:
        errors.append("price_or_nav_not_positive")
    if metric == "volume" and (numeric < 0 or numeric != numeric.to_integral_value()):
        errors.append("invalid_volume")
    if end > available.date():
        errors.append("period_ends_after_availability")
    key = stable_hash([source_id, entity, metric, start.isoformat(), end.isoformat(), currency, unit])
    return Observation(key, entity, metric, original, str(numeric), currency, unit, start.isoformat(), end.isoformat(),
                       published.isoformat(), available.isoformat(), "quarantined" if errors else "valid", tuple(errors), version,
                       row.get(f"supersedes_{metric}_id") or row.get("supersedes_id") or None)


def _csv_rows(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV headers missing or duplicated")
    return list(reader)


def _sec_companyfacts_rows(wrapper: dict) -> list[dict]:
    """Extract an explicitly selected filing from SEC companyfacts plus external release times."""
    facts = wrapper["companyfacts"]
    accession = str(wrapper.get("accession", ""))
    published = wrapper.get("published_at")
    available = wrapper.get("available_at")
    if not accession or not published or not available or not isinstance(facts, dict) or not facts.get("cik"):
        raise ValueError("SEC wrapper requires companyfacts, accession, CIK and release timestamps")
    rows = []
    for taxonomy, concepts in facts.get("facts", {}).items():
        for concept, details in concepts.items():
            for unit, entries in details.get("units", {}).items():
                for entry in entries:
                    if entry.get("accn") != accession:
                        continue
                    if unit in ("shares", "pure"):
                        currency = "XXX"
                    elif re.fullmatch(r"[A-Z]{3}(?:/shares)?", unit):
                        currency = unit[:3]
                    else:
                        raise ValueError(f"unsupported SEC unit for selected accession: {unit}")
                    end = entry.get("end")
                    if not end or "val" not in entry:
                        raise ValueError("selected SEC fact missing period or value")
                    rows.append({"entity_id": f"CIK:{int(facts['cik']):010d}", "metric": f"{taxonomy}:{concept}",
                                 "value": entry["val"], "currency": currency, "unit": unit,
                                 "period_start": entry.get("start") or end, "period_end": end,
                                 "published_at": published, "available_at": available, "version_identifier": accession})
    if not rows:
        raise ValueError("no supported facts for selected accession")
    return rows


def extract_observations(raw: bytes, source: Source, document: Document) -> list[Observation]:
    """Parse explicit provider-normalized CSV or JSON; never infer times or units."""
    source.validate(); document.validate()
    if len(raw) > MAX_BYTES:
        raise ValueError("document exceeds size limit")
    if document.document_type in ("financial_document", "corporate_announcement"):
        return []
    if document.document_type == "financial_statement":
        content = json.loads(raw.decode("utf-8-sig"), parse_float=str, parse_int=str)
        rows = content.get("rows") if isinstance(content, dict) else None
        if isinstance(content, dict) and "companyfacts" in content:
            rows = _sec_companyfacts_rows(content)
        if not isinstance(rows, list):
            raise ValueError("statement JSON must contain a rows array")
        result = [_observation(r, source.id, document, str(r.get("metric", "")), r["value"], str(r.get("unit", ""))) for r in rows]
    else:
        rows = _csv_rows(raw)
        result = []
        for row in rows:
            metrics = ("open", "high", "low", "close", "volume") if document.document_type == "market_prices" else ("nav",)
            vals = {}
            for metric in metrics:
                if row.get(metric) not in (None, ""):
                    unit = "shares" if metric == "volume" else row.get("unit", "")
                    observation = _observation(row, source.id, document, metric, row[metric], unit)
                    vals[metric] = Decimal(observation.numeric_value)
                    result.append(observation)
            if document.document_type == "market_prices":
                if "close" not in vals:
                    raise ValueError("market row requires close")
                if "high" in vals and "low" in vals and (vals["low"] > vals["high"] or any(not vals["low"] <= vals[m] <= vals["high"] for m in ("open", "close") if m in vals)):
                    raise ValueError("inconsistent OHLC row; import rejected")
            elif "nav" not in vals:
                raise ValueError("NAV row requires nav")
    identities = [(o.logical_key, o.version_identifier) for o in result]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate observation version in input")
    return result


def extract_text(raw: bytes, document: Document) -> str:
    document.validate()
    if len(raw) > MAX_BYTES:
        raise ValueError("document exceeds size limit")
    if document.media_type == "application/pdf":
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted or len(reader.pages) > 100:
            raise ValueError("encrypted PDF or more than 100 pages")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return raw.decode("utf-8-sig")


def extract_claims(text: str) -> list[Claim]:
    """Extract exact sentences containing finance terms and a digit; not fact verification."""
    claims = []
    for match in re.finditer(r"[^\n]+?(?:[.!?](?=\s|$)|(?=\n|$))", text):
        fragment = match.group()
        if re.search(r"\b(revenue|profit|loss|dividend|assets|liabilities|income|expenses|guidance|nav)\b", fragment, re.I) and re.search(r"\d", fragment):
            start = match.start() + len(fragment) - len(fragment.lstrip())
            end = match.end() - (len(fragment) - len(fragment.rstrip()))
            claims.append(Claim(text[start:end], start, end))
    return claims


def extract_announcements(text: str) -> list[dict]:
    """Classify literal announcement sentences; labels are unverified heuristics."""
    patterns = {"dividend": r"\bdividend\b", "results": r"\b(earnings|financial results)\b",
                "merger": r"\b(merger|acquisition)\b", "buyback": r"\b(buyback|share repurchase)\b",
                "guidance": r"\bguidance\b"}
    found = []
    for match in re.finditer(r"[^\n]+?(?:[.!?](?=\s|$)|(?=\n|$))", text):
        fragment = match.group()
        start = match.start() + len(fragment) - len(fragment.lstrip())
        end = match.end() - (len(fragment) - len(fragment.rstrip()))
        if start >= end:
            continue
        for event, pattern in patterns.items():
            if re.search(pattern, text[start:end], re.I):
                found.append({"event_type": event, "quote": text[start:end], "char_start": start, "char_end": end,
                              "extractor_version": EXTRACTOR_VERSION, "verification_status": "unverified"})
    return found


def preview(raw: bytes, source: Source, document: Document) -> dict:
    """Offline provenance and validation summary; no retrieval time is asserted."""
    observations = extract_observations(raw, source, document)
    narrative = document.document_type in {"financial_document", "corporate_announcement"}
    text = extract_text(raw, document) if narrative and source.allow_raw_storage else None
    return {"kind": "offline_preview_not_ingested", "source": asdict(source), "document": asdict(document),
            "content_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
            "observation_count": len(observations), "valid_count": sum(o.validation_status == "valid" for o in observations),
            "quarantined_count": sum(o.validation_status == "quarantined" for o in observations),
            "claim_count": len(extract_claims(text)) if text is not None else 0,
            "announcement_count": len(extract_announcements(text)) if text is not None and document.document_type == "corporate_announcement" else 0,
            "retrieved_at": None, "warning": "Preview is not a database ingestion or a point-in-time snapshot."}


def relation_graph(claim_ids: list[str], relations: list[dict]) -> nx.MultiDiGraph:
    graph = nx.MultiDiGraph()
    if len(set(claim_ids)) != len(claim_ids):
        raise ValueError("duplicate claim IDs")
    graph.add_nodes_from(claim_ids)
    for relation in relations:
        if (relation.get("from") not in graph or relation.get("to") not in graph or relation.get("from") == relation.get("to")
                or relation.get("relation") not in {"supports", "contradicts", "references"} or not relation.get("rationale", "").strip()):
            raise ValueError("invalid claim relation")
        graph.add_edge(relation["from"], relation["to"], relation=relation["relation"], rationale=relation["rationale"])
    return graph
