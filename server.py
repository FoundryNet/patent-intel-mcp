"""patent-intel-mcp — patent intelligence for autonomous agents.

Part of the FoundryNet Data Network. A FastMCP server over USPTO PatentsView data
cached in its own standalone Supabase project, with pgvector semantic prior-art
search. A daily in-process task (≈5am PT) ingests the last day's patents.

  search_patents       ($0.01)   patent search
  patent_detail        (free)    full record — drives adoption
  company_patents      ($0.01)   portfolio / filing velocity / tech areas
  trending_technology  ($0.01)   CPC classes by filing volume
  prior_art_search     ($0.02)   pgvector semantic similarity (premium)
  daily_digest         ($0.02)   structured daily filing digest
  mint_info            (free)    FoundryNet Data Network + sister-server info

Free tier 25 queries/day per agent, then a per-query paywall. Bearer fnet_ key
bypasses. Transport: Streamable HTTP at /mcp (+ legacy /sse). Health: /health.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import time

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import event_log

import config
import core
import daily_curator
import identity
import patent_aggregator
import patents_source
import payment_gate
import x402_standard
import supa
import tools

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("patent.mcp")

if not supa.configured():
    logger.warning("SUPABASE_SERVICE_KEY not set — dataset disabled until configured.")
if not patents_source.configured():
    logger.warning("PATENTSVIEW_API_KEY not set — daily aggregation will no-op until configured.")

mcp = FastMCP("patent-intel")

if payment_gate.is_active():
    logger.info(f"pay-per-query ARMED → {config.PAYMENT_RECIPIENT} after "
                f"{config.FREE_TIER_DAILY}/day free (search=${config.PRICE_SEARCH_PATENTS}, "
                f"prior_art=${config.PRICE_PRIOR_ART})")
else:
    logger.info("pay-per-query INERT — all tools free")

tools.register_all(mcp)


# ── okf-reliability-v1: emit reliability metadata on every tool result (#2964) ──
try:
    from okf_middleware import ReliabilityMiddleware
    mcp.add_middleware(ReliabilityMiddleware(server_id="patent-intel"))
except Exception as _okf_e:  # noqa: BLE001
    import logging as _okf_log; _okf_log.getLogger(__name__).warning(f"okf middleware not wired: {_okf_e}")


@mcp.custom_route("/v1/reliability", methods=["GET"])
async def _okf_reliability_route(request):
    from starlette.responses import JSONResponse
    import okf_endpoint
    return JSONResponse(okf_endpoint.reliability_payload("patent-intel"))


# ── Health ──────────────────────────────────────────────────────────────────
@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok", "service": "patent-intel-mcp", "transport": "streamable-http",
        "network": "FoundryNet Data Network",
        "tools": ["search_patents", "patent_detail", "company_patents",
                  "trending_technology", "prior_art_search", "daily_digest",
                  "daily_brief", "brief_summary", "mint_info"],
        "dataset": "supabase:patents" if supa.configured() else "unconfigured",
        "patentsview_key": "set" if patents_source.configured() else "unset",
        "embeddings": config.EMBED_MODEL,
        "x402_enabled": config.X402_ENABLED,
        "query_payment": "armed" if payment_gate.is_active() else "free",
        "prices_usdc": {"search_patents": config.PRICE_SEARCH_PATENTS,
                        "company_patents": config.PRICE_COMPANY_PATENTS,
                        "trending_technology": config.PRICE_TRENDING_TECH,
                        "prior_art_search": config.PRICE_PRIOR_ART,
                        "daily_digest": config.PRICE_DAILY_DIGEST},
        "free_tier_daily": config.FREE_TIER_DAILY,
        "payment_recipient": config.PAYMENT_RECIPIENT,
    })


@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


# ── REST surface ─────────────────────────────────────────────────────────────
_ERR_STATUS = {"bad_request": 400, "not_configured": 503, "not_found": 404,
               "payment_required": 402, "embedding_unavailable": 503}


def _resp(d: dict) -> JSONResponse:
    if "error" not in d:
        return JSONResponse(d, status_code=200)
    err = str(d.get("error") or "")
    code = _ERR_STATUS.get(err, 502 if err in ("network", "non_json_response", "unreachable") else 400)
    if err.startswith("http_") and err[5:].isdigit():
        code = int(err[5:])
    return JSONResponse(d, status_code=code)


async def _json_body(request: Request) -> dict:
    try:
        b = await request.json()
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def _akey(request: Request, body: dict) -> str:
    return identity.resolve_agent_key(body.get("agent_id"), request=request)


@mcp.custom_route("/v1/search", methods=["POST"])
async def rest_search(request: Request) -> JSONResponse:
    b = await _json_body(request)
    filters = {k: b.get(k) for k in ("keyword", "assignee", "cpc_code", "date_from",
                                     "date_to", "patent_type", "limit")}
    return _resp(await core.do_search(filters, agent_key=_akey(request, b),
                                      payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/detail", methods=["POST"])
async def rest_detail(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_detail(b.get("patent_number", "")))


@mcp.custom_route("/v1/company", methods=["POST"])
async def rest_company(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_company(b.get("company_name", ""), b.get("days_back"),
                                       agent_key=_akey(request, b),
                                       payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/trending", methods=["POST"])
async def rest_trending(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_trending(b.get("days", 30), b.get("min_filings", 1),
                                        agent_key=_akey(request, b),
                                        payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/prior-art", methods=["POST"])
async def rest_prior_art(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_prior_art(b.get("description", ""), b.get("max_results", 10),
                                         agent_key=_akey(request, b),
                                         payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/digest", methods=["POST"])
async def rest_digest(request: Request) -> JSONResponse:
    b = await _json_body(request)
    return _resp(await core.do_digest(b.get("cpc_code"), b.get("assignee"),
                                      agent_key=_akey(request, b),
                                      payment_tx=b.get("payment_tx"), api_key=identity.bearer(request)))


@mcp.custom_route("/v1/mint-info", methods=["GET", "POST"])
async def rest_mint(request: Request) -> JSONResponse:
    return JSONResponse(core.mint_info())


@mcp.custom_route("/admin/aggregate", methods=["POST"])
async def admin_aggregate(request: Request) -> JSONResponse:
    """Manually trigger an ingestion run (also runs daily in-process). Guarded by
    the ADMIN_TOKEN env var via the X-Admin-Token header. ?days=N to backfill."""
    import os
    tok = os.environ.get("ADMIN_TOKEN", "")
    if not tok or request.headers.get("x-admin-token") != tok:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    try:
        days = int(request.query_params.get("days", "1"))
    except ValueError:
        days = 1
    if request.query_params.get("wait") == "1":
        res = await patent_aggregator.run_aggregation(days)
        return JSONResponse(res)
    # Fire-and-forget so a long embed/upsert run isn't killed by the edge timeout.
    asyncio.create_task(patent_aggregator.run_aggregation(days))
    return JSONResponse({"started": True, "days": days,
                         "note": "running in background; poll /health or the dataset"})


# ── Discovery (with FoundryNet Data Network cross-promo) ─────────────────────
_TAGLINE = "Patent search, USPTO data & prior-art search for agents."
_DESC = ("Patent intelligence for agents: patent search, USPTO PatentsView data, "
         "patent landscape, technology trends, and pgvector semantic prior-art "
         "search. Part of the FoundryNet Data Network; see also gov-contracts-mcp "
         "and brand-intel-mcp.")

_AGENT_CARD = {
    "name": "Patent Intelligence MCP",
    "description": ("Search USPTO patents, citations, and assignees, and find prior art "
                    "with semantic vector search — for IP research and patent-landscape "
                    "analysis."),
    "url": config.PUBLIC_MCP_URL,
    "version": "1.0.0",
    "capabilities": {
        "tools": ["search_patents", "patent_detail", "company_patents",
                  "trending_technology", "prior_art_search", "daily_digest",
                  "daily_brief", "brief_summary", "mint_info"],
    },
    "provider": {"name": "FoundryNet", "url": "https://foundrynet.io"},
    "network": "FoundryNet Data Network",
    "attestation": {"verified_outputs": True},
    "protocols": {
        "mcp": {"endpoint": config.PUBLIC_MCP_URL, "transport": "streamable-http", "tools_count": 9},
        "x402": {"supported": True},
    },
    "see_also": config.SISTER_SERVERS,
    "contact": "forge@foundrynet.io",
}


@mcp.custom_route("/.well-known/agent-card.json", methods=["GET"])
async def agent_card(request: Request) -> JSONResponse:
    return JSONResponse(_AGENT_CARD, headers={"Cache-Control": "public, max-age=300"})


@mcp.custom_route("/.well-known/mcp", methods=["GET"])
async def mcp_endpoints(request: Request) -> JSONResponse:
    return JSONResponse({"endpoints": [{"url": config.PUBLIC_MCP_URL,
                                        "transport": "streamable-http",
                                        "name": "Patent Intelligence MCP"}]},
                        headers={"Cache-Control": "public, max-age=300"})


async def _live_tools() -> list:
    res = mcp.list_tools()
    if inspect.iscoroutine(res):
        res = await res
    return [{"name": t.name, "description": (getattr(t, "description", "") or "").strip(),
             "inputSchema": getattr(t, "parameters", None) or {"type": "object"}} for t in res]


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def server_card(request: Request) -> JSONResponse:
    live = await _live_tools()
    return JSONResponse({
        "serverInfo": {"name": "Patent Intelligence MCP", "version": "1.0.0"},
        "authentication": {"type": "http", "scheme": "bearer",
                           "description": ("patent_detail and mint_info are free; other tools "
                                           "give 25 free queries/day then take an fnet_ Bearer key "
                                           "OR a per-query payment.")},
        "tools": live, "version": "1.0", "name": "Patent Intelligence MCP",
        "tagline": _TAGLINE, "description": _DESC,
        "serverUrl": config.PUBLIC_MCP_URL, "transport": "streamable-http",
        "tools_count": len(live),
        "categories": ["patents", "intellectual-property", "data", "research", "legal"],
        "keywords": ["patent search", "USPTO data", "intellectual property",
                     "patent landscape", "prior art search", "technology trends",
                     "patent intelligence"],
        "network": "FoundryNet Data Network",
        "see_also": config.SISTER_SERVERS,
        "pricing": {"model": "metered",
                    "free_tier": f"{config.FREE_TIER_DAILY} queries/day + free patent_detail",
                    "paid_from": f"{config.PRICE_SEARCH_PATENTS} per query"},
    }, headers={"Cache-Control": "public, max-age=300"})


# ── Daily aggregation (≈5am PT = AGG_HOUR_UTC) ───────────────────────────────
async def _agg_loop():
    while True:
        now = time.gmtime()
        secs_today = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
        target = config.AGG_HOUR_UTC * 3600
        wait = (target - secs_today) % 86400 or 86400
        try:
            await asyncio.sleep(wait)
            if supa.configured() and patents_source.configured():
                await patent_aggregator.run_aggregation()
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"aggregation loop error: {e}")
            await asyncio.sleep(3600)


_FREE_TOOL_NAMES = {"mint_info", "macro_dashboard", "cve_detail", "detail",
                    "domain_age", "convert", "rates", "market_overview", "price",
                    "quote", "batch_quote", "sector_performance"}


@mcp.custom_route("/.well-known/mcp.json", methods=["GET"])
async def wellknown_mcp_json(request: Request) -> JSONResponse:
    """Machine-discovery card (emerging standard) for AI clients/crawlers."""
    live = await _live_tools()
    names = [t["name"] for t in live]
    return JSONResponse({
        "name": _AGENT_CARD["name"],
        "description": _AGENT_CARD["description"],
        "url": config.PUBLIC_MCP_URL,
        "transport": ["streamable-http"],
        "tools": names,
        "pricing": {"model": "per-query", "free_tier": True,
                    "paid_tools": [n for n in names if n not in _FREE_TOOL_NAMES]},
        "attestation": {"enabled": True},
        "network": {"name": "FoundryNet Data Network", "servers": 17,
                    "homepage": "https://foundrynet.io"},
    }, headers={"Cache-Control": "public, max-age=300"})



# ── Standard x402 compliance (discoverable on x402scan / 402 Index / CDP Bazaar) ──
@mcp.custom_route("/x402", methods=["GET"])
async def x402_index(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/.well-known/x402", methods=["GET"])
async def x402_wellknown(request: Request) -> JSONResponse:
    return JSONResponse(x402_standard.index(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*"})


@mcp.custom_route("/x402/{tool}", methods=["GET", "POST"])
async def x402_resource(request: Request) -> JSONResponse:
    tool = request.path_params["tool"]
    if tool not in x402_standard.PAID_TOOLS:
        return JSONResponse({"error": "unknown_resource", "tool": tool,
                             "available": list(x402_standard.PAID_TOOLS)}, status_code=404)
    challenge = x402_standard.payment_required_header(tool)
    return JSONResponse(x402_standard.payment_required(tool), status_code=402,
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "PAYMENT-REQUIRED": challenge,
                                 "X-PAYMENT": challenge,
                                 "Link": '</openapi.json>; rel="describedby"',
                                 "WWW-Authenticate": 'x402 version="2"'})


@mcp.custom_route("/openapi.json", methods=["GET"])
async def openapi_doc(request: Request) -> JSONResponse:
    """OpenAPI 3.1 discovery doc — x402scan requires a spec at a discoverable URL."""
    return JSONResponse(x402_standard.openapi(),
                        headers={"Cache-Control": "public, max-age=300",
                                 "Access-Control-Allow-Origin": "*",
                                 "Link": '</openapi.json>; rel="describedby"'})


def build_dual_app():
    main_app = mcp.http_app(transport="http", path="/mcp")
    sse_app = mcp.http_app(transport="sse", path="/sse")
    for r in sse_app.routes:
        if getattr(r, "path", None) in ("/sse", "/messages"):
            main_app.router.routes.append(r)
    main_life, sse_life = main_app.router.lifespan_context, sse_app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def _dual_lifespan(app):
        async with main_life(app):
            async with sse_life(app):
                task = asyncio.create_task(_agg_loop())
                brief_task = asyncio.create_task(daily_curator.curator_loop())
                try:
                    yield
                finally:
                    for t in (task, brief_task):
                        t.cancel()
                        with contextlib.suppress(Exception):
                            await t
    main_app.router.lifespan_context = _dual_lifespan
    # Per-call telemetry middleware (fire-and-forget to agents ingest).
    main_app.add_middleware(BaseHTTPMiddleware, dispatch=event_log.middleware)
    return main_app


if __name__ == "__main__":
    import uvicorn
    logger.info(f"patent-intel-mcp starting on 0.0.0.0:{config.PORT} "
                f"(dataset={'supabase' if supa.configured() else 'off'}, x402={config.X402_ENABLED})")
    uvicorn.run(build_dual_app(), host="0.0.0.0", port=config.PORT, log_level="warning")
