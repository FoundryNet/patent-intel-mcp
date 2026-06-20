"""Daily curated brief — patent-intel.

Runs once a day at BRIEF_HOUR_UTC (05:00 UTC) as an in-process background task
(same shape as the aggregation loop). It surveys the most recent patent activity,
ranks the most significant filings, surfaces filing-velocity anomalies, trending
CPC classes, and major assignee activity, attests the package through MINT for
verifiable provenance, and upserts it into the `daily_briefs` table. The paid
`daily_brief` tool just reads that row back.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import config
import mint_integration
import supa

logger = logging.getLogger("patent.curator")

SERVER = config.SERVER_SLUG
PRICE = config.PRICE_DAILY_BRIEF

# Window for "recent" patent activity. Patent grant/publication data is not
# minute-granular, so the curator looks back a few days and ranks within it.
_RECENT_DAYS = 7


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _expires_at(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return (d + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")


def related_briefs(exclude: str) -> list:
    return [{"server": s, "price": p, "tool": "daily_brief"}
            for s, p in config.NETWORK_BRIEFS.items() if s != exclude]


async def _curate_signals(since_iso: str) -> tuple[dict, int]:
    """Build the patent brief body from the most recent filings. Returns (signals,
    count). Patent data lacks 24h granularity, so we survey the most-recent window
    and rank within it."""
    recent_from = _days_ago(_RECENT_DAYS)
    rows = await supa.recent_for_trending(recent_from)

    # significant_filings: most-recent, most-cited filings (citation_count as the
    # significance proxy, then recency).
    sig = sorted(rows, key=lambda r: ((r.get("citation_count") or 0),
                                      (r.get("grant_date") or r.get("filing_date") or "")),
                 reverse=True)
    significant_filings = [
        {"patent_number": r.get("patent_number"), "title": r.get("title"),
         "assignee_name": r.get("assignee_name"), "cpc_primary": r.get("cpc_primary"),
         "grant_date": r.get("grant_date"), "citation_count": r.get("citation_count")}
        for r in sig[:10]]

    # velocity_anomalies: assignees with unusually large recent filing spikes,
    # measured against their 90d baseline velocity from patent_assignees.
    recent_by_assignee = Counter(r.get("assignee_name") for r in rows if r.get("assignee_name"))
    velocity_anomalies = []
    summaries = await supa.select(
        "patent_assignees",
        {"select": "assignee_name,filing_velocity_90d,patent_count",
         "order": "filing_velocity_90d.desc.nullslast", "limit": "500"})
    baseline = {s.get("assignee_name"): (s.get("filing_velocity_90d") or 0) for s in summaries}
    for name, recent_n in recent_by_assignee.most_common(50):
        base90 = baseline.get(name, 0)
        # Expected filings in this window if filing at the 90d-average rate.
        expected = (base90 / 90.0) * _RECENT_DAYS
        if recent_n >= 3 and recent_n >= max(2 * expected, 3):
            velocity_anomalies.append({
                "assignee_name": name, "recent_filings": recent_n,
                "expected_filings": round(expected, 1),
                "velocity_90d": base90,
                "spike_ratio": round(recent_n / expected, 1) if expected else None})
    velocity_anomalies = velocity_anomalies[:10]

    # trending_cpc_classes: CPC primary codes by recent filing volume.
    cpc_buckets: dict = defaultdict(lambda: {"filings": 0, "assignees": Counter()})
    for r in rows:
        code = r.get("cpc_primary")
        if not code:
            continue
        b = cpc_buckets[code]
        b["filings"] += 1
        if r.get("assignee_name"):
            b["assignees"][r["assignee_name"]] += 1
    trending = sorted(cpc_buckets.items(), key=lambda kv: kv[1]["filings"], reverse=True)
    trending_cpc_classes = [
        {"cpc_code": code, "filings": b["filings"],
         "top_assignees": [{"name": n, "filings": c} for n, c in b["assignees"].most_common(3)]}
        for code, b in trending[:10]]

    # major_assignee_activity: most active assignees in the recent window.
    major_assignee_activity = [
        {"assignee_name": name, "recent_filings": n,
         "velocity_90d": baseline.get(name, 0)}
        for name, n in recent_by_assignee.most_common(10)]

    signals = {
        "significant_filings": significant_filings,
        "velocity_anomalies": velocity_anomalies,
        "trending_cpc_classes": trending_cpc_classes,
        "major_assignee_activity": major_assignee_activity,
        "window": {"since": recent_from, "patents_surveyed": len(rows)},
    }
    count = (len(significant_filings) + len(velocity_anomalies)
             + len(trending_cpc_classes) + len(major_assignee_activity))
    return signals, count


async def run_curation(date_str: str | None = None) -> dict:
    """Generate, attest, and store today's brief. Idempotent per date (upsert)."""
    date_str = date_str or _today()
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
    signals, count = await _curate_signals(since_iso)

    brief = {
        "brief_date": date_str, "server": SERVER, "signal_count": count,
        "signals": signals, "expires_at": _expires_at(date_str),
        "related_briefs": related_briefs(SERVER),
    }
    # Attest for provenance (sync httpx → run off the event loop; fail-open).
    attestation = await asyncio.to_thread(
        mint_integration.attest_data, brief, "analysis",
        f"Daily {SERVER} brief: {count} signals")
    brief["provenance"] = attestation

    row = {
        "brief_date": date_str, "brief_data": brief, "signal_count": count,
        "attestation_hash": attestation.get("attestation_hash"),
        "expires_at": _expires_at(date_str),
    }
    res = await supa.upsert("daily_briefs", [row], "brief_date")
    if isinstance(res, dict) and res.get("error"):
        logger.warning(f"daily brief upsert failed: {str(res)[:200]}")
    else:
        logger.info(f"daily brief stored: {date_str} ({count} signals, "
                    f"attested={attestation.get('mint_verified')})")
    return brief


async def get_brief(date_str: str | None = None) -> dict | None:
    """Read a stored brief; None if missing or expired."""
    date_str = date_str or _today()
    rows = await supa.select("daily_briefs",
                             {"select": "*", "brief_date": f"eq.{date_str}", "limit": "1"})
    if not rows:
        return None
    row = rows[0]
    exp = row.get("expires_at")
    if exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(exp.replace("Z", "+00:00")):
                return None
        except Exception:  # noqa: BLE001
            pass
    return row.get("brief_data")


async def bump_purchase(date_str: str) -> None:
    """Best-effort purchase counter via RPC (no-op if the function is absent)."""
    try:
        await supa.rpc("increment_brief_purchase", {"p_brief_date": date_str})
    except Exception:  # noqa: BLE001
        pass


async def curator_loop() -> None:
    """Sleep until BRIEF_HOUR_UTC each day, then curate. Cancellable."""
    while True:
        now = datetime.now(timezone.utc)
        secs = now.hour * 3600 + now.minute * 60 + now.second
        wait = (config.BRIEF_HOUR_UTC * 3600 - secs) % 86400 or 86400
        try:
            await asyncio.sleep(wait)
            if supa.configured():
                await run_curation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"curator loop error: {e}")
            await asyncio.sleep(3600)
