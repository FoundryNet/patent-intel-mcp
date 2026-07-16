"""Shared logic behind the MCP tools + REST routes: the 7 operations and the x402
gating. Paid tools run payment_gate.precheck(price) before querying; patent_detail
and mint_info are free.
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import config
import daily_curator
import embed
import mint_integration
import payment_gate
import stripe_gate
import supa

logger = logging.getLogger("patent.core")

# Coarse CPC section descriptions (first letter) — enough to label trending
# clusters without shipping the full CPC dictionary.
CPC_SECTIONS = {
    "A": "Human Necessities", "B": "Performing Operations; Transporting",
    "C": "Chemistry; Metallurgy", "D": "Textiles; Paper",
    "E": "Fixed Constructions", "F": "Mechanical Engineering; Lighting; Heating; Weapons",
    "G": "Physics", "H": "Electricity", "Y": "Emerging Cross-Sectional Technologies",
}


def _cpc_desc(code: Optional[str]) -> Optional[str]:
    return CPC_SECTIONS.get(code[0].upper()) if code else None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


def _billing(d: dict) -> dict:
    g = d.get("gate")
    if g == "free":
        cap, cnt = d.get("cap"), d.get("count")
        return {"tier": "free", "used_today": cnt, "daily_free": cap,
                "remaining_today": (cap - cnt) if (cap is not None and cnt is not None) else None}
    if g == "paid":
        return {"tier": "paid", "charged_usdc": d.get("amount_usdc")}
    if g == "api_key":
        return {"tier": "api_key", "note": "billed to your Forge account"}
    return {"tier": "free", "note": "gating inert"}


async def do_search(filters: dict, *, agent_key, payment_tx=None, api_key=None) -> dict:
    params = {k: v for k, v in (filters or {}).items() if v not in (None, "")}
    dec = await payment_gate.precheck("search_patents", params, config.PRICE_SEARCH_PATENTS,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    rows = await supa.search_patents(**params)
    return {"results": rows, "count": len(rows), "billing": _billing(dec)}


async def do_detail(patent_number: str) -> dict:
    if not patent_number:
        return {"error": "bad_request", "detail": "patent_number is required"}
    row = await supa.patent_by_number(str(patent_number))
    if not row:
        return {"error": "not_found", "detail": f"No patent {patent_number!r} in the dataset"}
    return {"patent": row}


async def do_company(company_name: str, days_back: Optional[int], *, agent_key,
                     payment_tx=None, api_key=None) -> dict:
    if not company_name:
        return {"error": "bad_request", "detail": "company_name is required"}
    params = {"company_name": company_name, "days_back": days_back}
    params = {k: v for k, v in params.items() if v not in (None, "")}
    dec = await payment_gate.precheck("company_patents", params, config.PRICE_COMPANY_PATENTS,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    date_from = _days_ago(int(days_back)) if days_back else None
    rows = await supa.company_patents(company_name, date_from, limit=500)
    summary = await supa.assignee_summary(company_name)
    cpc_counts = Counter(r.get("cpc_primary") for r in rows if r.get("cpc_primary"))
    recent = sorted(rows, key=lambda r: (r.get("grant_date") or ""), reverse=True)[:15]
    return {
        "company_name": company_name,
        "patent_count": (summary or {}).get("patent_count", len(rows)),
        "filing_velocity_90d": (summary or {}).get("filing_velocity_90d"),
        "latest_filing_date": (summary or {}).get("latest_filing_date"),
        "primary_technology_areas": [
            {"cpc": c, "description": _cpc_desc(c), "count": n}
            for c, n in cpc_counts.most_common(8)],
        "recent_filings": [
            {"patent_number": r.get("patent_number"), "title": r.get("title"),
             "grant_date": r.get("grant_date"), "cpc_primary": r.get("cpc_primary")}
            for r in recent],
        "window_matches": len(rows),
        "billing": _billing(dec),
    }


async def do_trending(days: int, min_filings: int, *, agent_key, payment_tx=None, api_key=None) -> dict:
    days = min(max(int(days or 30), 1), 365)
    min_filings = max(int(min_filings or 1), 1)
    dec = await payment_gate.precheck("trending_technology", {"days": days, "min_filings": min_filings},
                                      config.PRICE_TRENDING_TECH, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    rows = await supa.recent_for_trending(_days_ago(days))
    buckets: dict = defaultdict(lambda: {"filings": 0, "assignees": Counter()})
    for r in rows:
        code = r.get("cpc_primary")
        if not code:
            continue
        b = buckets[code]
        b["filings"] += 1
        if r.get("assignee_name"):
            b["assignees"][r["assignee_name"]] += 1
    classes = []
    for code, b in buckets.items():
        if b["filings"] < min_filings:
            continue
        classes.append({"cpc_code": code, "description": _cpc_desc(code),
                        "filings": b["filings"],
                        "top_assignees": [{"name": n, "filings": c}
                                          for n, c in b["assignees"].most_common(5)]})
    classes.sort(key=lambda x: x["filings"], reverse=True)
    return {"since": _days_ago(days), "days": days, "total_filings": len(rows),
            "cpc_classes": classes[:25], "billing": _billing(dec)}


async def do_prior_art(description: str, max_results: Optional[int], *, agent_key,
                       payment_tx=None, api_key=None) -> dict:
    if not description or not description.strip():
        return {"error": "bad_request", "detail": "description is required"}
    n = min(max(int(max_results or 10), 1), 50)
    # intent params keyed on a hash of the description so the memo is stable.
    import hashlib
    dkey = hashlib.sha256(description.strip().encode()).hexdigest()[:16]
    dec = await payment_gate.precheck("prior_art_search", {"d": dkey, "n": n},
                                      config.PRICE_PRIOR_ART, agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    vec = await asyncio.to_thread(embed.embed_one, description)
    if vec is None:
        return {"error": "embedding_unavailable",
                "detail": "Could not embed the description for semantic search."}
    matches = await supa.match_patents(vec, n)
    result = {"query": description[:200], "results": matches, "count": len(matches),
              "method": "pgvector cosine similarity on patent abstracts",
              "billing": _billing(dec)}
    # Provenance attestation (additive; fail-open; off the event loop).
    result["provenance"] = await asyncio.to_thread(
        mint_integration.attest_data, result, "analysis", "prior_art_search query result")
    return result


async def do_digest(cpc: Optional[str], assignee: Optional[str], *, agent_key,
                    payment_tx=None, api_key=None) -> dict:
    params = {k: v for k, v in {"cpc_code": cpc, "assignee": assignee}.items() if v}
    dec = await payment_gate.precheck("daily_digest", params, config.PRICE_DAILY_DIGEST,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    since = _days_ago(1)
    rows = await supa.digest_patents(cpc=cpc, assignee=assignee, date_from=since, limit=100)
    by_cpc = Counter(r.get("cpc_primary") for r in rows if r.get("cpc_primary"))
    by_assignee = Counter(r.get("assignee_name") for r in rows if r.get("assignee_name"))
    return {
        "date": _today(), "since": since, "total_new": len(rows),
        "filters": {"cpc_code": cpc, "assignee": assignee},
        "top_cpc": [{"cpc": c, "description": _cpc_desc(c), "count": n} for c, n in by_cpc.most_common(10)],
        "top_assignees": [{"name": a, "count": n} for a, n in by_assignee.most_common(10)],
        "patents": [{"patent_number": r.get("patent_number"), "title": r.get("title"),
                     "assignee_name": r.get("assignee_name"), "cpc_primary": r.get("cpc_primary"),
                     "grant_date": r.get("grant_date")} for r in rows[:50]],
        "billing": _billing(dec),
    }


# ── daily_brief (premium, curated) ────────────────────────────────────────────
async def do_daily_brief(date, *, agent_key, payment_tx=None, api_key=None,
                         stripe_token=None) -> dict:
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()

    # Stripe rail (parallel to x402): a paid Checkout Session unlocks the brief.
    stripe_err = None
    if stripe_token and stripe_gate.is_active():
        sv = await stripe_gate.verify_session(stripe_token, config.PRICE_DAILY_BRIEF,
                                              tool="daily_brief", agent_key=agent_key)
        if sv["ok"]:
            brief = await daily_curator.get_brief(day)
            if not brief:
                return {"error": "not_available",
                        "detail": f"No brief for {day} (not yet generated, or expired at midnight UTC). "
                                  f"Briefs are curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                        "billing": "stripe"}
            await daily_curator.bump_purchase(day)
            return {**brief, "billing": "stripe", "stripe_session": sv["session"]}
        stripe_err = sv.get("detail")  # surface on the 402 below

    dec = await payment_gate.precheck("daily_brief", {"date": day}, config.PRICE_DAILY_BRIEF,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return stripe_gate.augment_402(dec["body"], config.PRICE_DAILY_BRIEF,
                                       stripe_error=stripe_err)
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} (not yet generated, or expired at midnight UTC). "
                          f"Briefs are curated daily at {config.BRIEF_HOUR_UTC:02d}:00 UTC.",
                "billing": _billing(dec)}
    await daily_curator.bump_purchase(day)
    return {**brief, "billing": _billing(dec)}


def mint_info() -> dict:
    """FREE — FoundryNet Data Network info."""
    return {
        "network": "FoundryNet Data Network",
        "message": ("Every premium result from this server carries verifiable "
                    "provenance, so a buyer can confirm it was produced here, "
                    "unaltered."),
        "see_also": config.SISTER_SERVERS,
    }


# ── Soft upsell: surface the daily_brief on every paid, non-brief response ─────
# Appends one non-blocking `available_intelligence` field to successful paid tool
# responses so the calling agent learns a single curated brief can replace many
# individual paid queries. Skips error and 402/payment_required bodies, and never
# touches daily_brief itself (no self-upsell). Brief status is cached 5 min, so
# this adds no per-call DB latency. Added 2026-06-20 (seller_agent v2 upsell hook).
import time as _upsell_time

_brief_upsell_cache = {"day": None, "ts": 0.0, "available": False, "count": 0}


async def _brief_status_cached() -> tuple[bool, int]:
    day = _upsell_time.strftime("%Y-%m-%d", _upsell_time.gmtime())
    now = _upsell_time.time()
    c = _brief_upsell_cache
    if c["day"] == day and (now - c["ts"]) < 300:
        return c["available"], c["count"]
    avail, count = False, 0
    try:
        brief = await daily_curator.get_brief(day)
        if brief:
            avail, count = True, int(brief.get("signal_count") or 0)
    except Exception:  # noqa: BLE001
        return c["available"], c["count"]
    c.update(day=day, ts=now, available=avail, count=count)
    return avail, count


async def _available_intelligence() -> dict:
    avail, count = await _brief_status_cached()
    return {"daily_brief": {
        "available": avail,
        "signal_count": count,
        "price_usd": config.PRICE_DAILY_BRIEF,
        "tool": "daily_brief",
        "note": "Curated daily intelligence — more efficient than individual queries",
    }}


def _make_upsell(_fn):
    import functools

    @functools.wraps(_fn)
    async def _wrapped(*a, **k):
        result = await _fn(*a, **k)
        if isinstance(result, dict) and "error" not in result and "payment_required" not in result:
            try:
                result["available_intelligence"] = await _available_intelligence()
            except Exception:  # noqa: BLE001
                pass
            try:
                import asyncio as _aio, mint_integration as _mint, upsell_engine as _upsell_engine
                _hb = await _aio.to_thread(_mint.network_heartbeat)
                _av, _ct = await _brief_status_cached()
                result["foundrynet_network"] = {**_hb, **_upsell_engine.get_upsell(
                    brief_price=config.PRICE_DAILY_BRIEF, brief_signal_count=(_ct if _av else None))}
            except Exception:  # noqa: BLE001
                pass
        return result

    return _wrapped


for _upsell_fn in ("do_search", "do_company", "do_trending", "do_prior_art", "do_digest",):
    if _upsell_fn in globals():
        globals()[_upsell_fn] = _make_upsell(globals()[_upsell_fn])


# ── brief_summary ($0.50): structured top-5 sample of today's brief (upsell) ──
def _top_signals(brief: dict, n: int = 5) -> list:
    """Flatten a brief's signals into a flat top-N list — structure-agnostic."""
    sig = (brief or {}).get("signals")
    items: list = []
    if isinstance(sig, dict):
        for cat, val in sig.items():
            if isinstance(val, list):
                for it in val:
                    items.append({"category": cat, **(it if isinstance(it, dict) else {"value": it})})
            elif isinstance(val, dict):
                items.append({"category": cat, **val})
            elif val not in (None, "", 0):
                items.append({"category": cat, "value": val})
    elif isinstance(sig, list):
        items = sig
    return items[:n]


async def do_brief_summary(date, *, agent_key, payment_tx=None, api_key=None):
    """Top-5 signals from today's brief as structured JSON (no prose) — the $0.50
    sample that upsells the full daily_brief."""
    from datetime import datetime, timezone
    day = (date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    dec = await payment_gate.precheck("brief_summary", {"date": day}, config.PRICE_BRIEF_SUMMARY,
                                      agent_key, payment_tx, api_key)
    if dec["gate"] == "blocked":
        return dec["body"]
    brief = await daily_curator.get_brief(day)
    if not brief:
        return {"error": "not_available",
                "detail": f"No brief for {day} yet (curated daily; expires next midnight UTC).",
                "billing": _billing(dec)}
    return {
        "date": day,
        "top_signals": _top_signals(brief, 5),
        "total_signals": brief.get("signal_count"),
        "full_brief": {"tool": "daily_brief", "price_usd": config.PRICE_DAILY_BRIEF,
                       "note": "Full brief returns all signals with complete detail + provenance attestation."},
        "billing": _billing(dec),
    }
