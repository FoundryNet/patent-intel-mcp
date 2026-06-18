#!/usr/bin/env python3
"""patent_aggregator — daily USPTO patent ingestion (cron 5am PT / ~12:00 UTC).

Each run: fetch patents granted in the last PATENTS_LOOKBACK_DAYS from PatentsView,
map to the schema, embed abstracts (fastembed) for prior-art search, upsert into
Supabase `patents`, then recompute the `patent_assignees` rolling summary.

The patent-intel-mcp server runs run_aggregation() in-process daily; this script
is the standalone/manual entry point:
  python patent_aggregator.py            # last PATENTS_LOOKBACK_DAYS
  python patent_aggregator.py 7          # last 7 days (backfill)
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

import config
import embed
import patents_source
import supa

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("patent.agg")


async def run_aggregation(lookback_days: int | None = None) -> dict:
    days = lookback_days or config.PATENTS_LOOKBACK_DAYS
    date_from = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    log.info(f"aggregating patents granted since {date_from}")

    rows = await patents_source.fetch_recent(date_from)
    if not rows:
        log.info("no patents fetched (no key, empty window, or API error)")
        return {"fetched": 0, "upserted": 0}

    # Embed abstracts (blocking ONNX inference → worker thread).
    abstracts = [r.get("abstract") or r.get("title") or "" for r in rows]
    vecs = await asyncio.to_thread(embed.embed_many, abstracts)
    for r, v in zip(rows, vecs):
        if v is not None:
            r["embedding"] = "[" + ",".join(f"{x:.6f}" for x in v) + "]"

    written = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        res = await supa.upsert_patents(chunk)
        if "error" in res:
            log.warning(f"upsert chunk {i} failed: {res}")
        else:
            written += len(chunk)
    log.info(f"upserted {written}/{len(rows)} patents")

    await supa.refresh_assignee_summary()
    log.info("refreshed patent_assignees summary")
    return {"fetched": len(rows), "upserted": written}


async def main() -> None:
    args = [a for a in sys.argv[1:] if a.strip()]
    days = int(args[0]) if args and args[0].isdigit() else None
    res = await run_aggregation(days)
    print(res)


if __name__ == "__main__":
    asyncio.run(main())
