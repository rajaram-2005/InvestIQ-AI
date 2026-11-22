"""Trusted local/server ingestion utility; never run with client-exposed service credentials."""
import argparse
import json
import os
from pathlib import Path

import psycopg
from .core import Source, Document, preview
from .store import ingest, create_snapshot, get_snapshot, provenance_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("preview")
    inspect.add_argument("--metadata", type=Path, required=True)
    inspect.add_argument("--file", type=Path, required=True)
    ing = sub.add_parser("ingest")
    ing.add_argument("--metadata", type=Path, required=True)
    ing.add_argument("--file", type=Path, required=True)
    ing.add_argument("--owner", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("--owner", required=True)
    snap.add_argument("--as-of", required=True)
    report = sub.add_parser("report")
    report.add_argument("--owner", required=True)
    report.add_argument("--snapshot-id", required=True)
    args = parser.parse_args()
    if args.command == "preview":
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        print(json.dumps(preview(args.file.read_bytes(), Source(**metadata["source"]), Document(**metadata["document"])), indent=2, sort_keys=True))
        return
    dsn = os.environ.get("INVESTIQ_DATABASE_URL")
    if not dsn:
        parser.error("INVESTIQ_DATABASE_URL must be set on the trusted server")
    with psycopg.connect(dsn, autocommit=True) as conn:
        if args.command == "ingest":
            metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
            result = ingest(conn, args.owner, Source(**metadata["source"]), Document(**metadata["document"]), args.file.read_bytes())
        elif args.command == "snapshot":
            snapshot = create_snapshot(conn, args.owner, args.as_of)
            result = {"snapshot_id": snapshot["snapshot_id"], "report": provenance_report(snapshot["manifest"])}
        else:
            result = provenance_report(get_snapshot(conn, args.owner, args.snapshot_id))
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
