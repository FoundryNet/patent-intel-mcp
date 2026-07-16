# Patent Intelligence MCP

**Patent intelligence for AI agents** — patent search, USPTO data, patent
landscape, technology trends, and **semantic prior-art search** (pgvector) over
USPTO PatentsView data.

> Part of the **FoundryNet Data Network**. Every result carries verifiable
> provenance so you can confirm it was produced by this server, unaltered.
> See also: **gov-contracts-mcp**, **brand-intel-mcp**.

## Connect

- **MCP endpoint** (Streamable HTTP): `https://patent-intel-mcp-production.up.railway.app/mcp`
- **MCP registry**: `io.github.FoundryNet/patent-intel-mcp`
- **Agent card**: `https://patent-intel-mcp-production.up.railway.app/.well-known/agent-card.json`

### Claude Desktop / Cursor / Claude Code

```bash
claude mcp add --transport http patent-intel https://patent-intel-mcp-production.up.railway.app/mcp
```

```json
{ "mcpServers": { "patent-intel": { "url": "https://patent-intel-mcp-production.up.railway.app/mcp" } } }
```

## Tools

| Tool | Price | What it does |
|---|---|---|
| `search_patents` | $0.01 | Search by keyword, assignee, CPC, date, type |
| `patent_detail` | **free** | Full record: claims, citations, inventors |
| `company_patents` | $0.01 | Portfolio: count, filing velocity, tech areas, recent filings |
| `trending_technology` | $0.01 | CPC classes ranked by filing volume, with top assignees |
| `prior_art_search` | $0.05 | Semantic similarity over abstracts (pgvector) — **premium** |
| `daily_digest` | $0.05 | Structured daily patent-filing digest |
| `brief_summary` | $0.50 | Sample slice of the curated daily patent-intel brief |
| `daily_brief` | $10 | Full curated daily patent-intel brief — **premium** |
| `mint_info` | **free** | FoundryNet Data Network details |

**Free tier:** 25 paid-tool queries/day per agent (plus free `patent_detail` +
`mint_info`). Pass `agent_id` to scope your allowance. After that, the tool
returns an HTTP-402 with a payment challenge — settle it, then re-call with the
same args plus `payment_tx=<reference>`. An `Authorization: Bearer fnet_…` key
bypasses the paywall.

## How it works

A daily task (~5am PT) ingests patents granted in the last day from the **USPTO
PatentsView Search API**, maps them to a normalized schema, embeds abstracts with
**fastembed** (BAAI/bge-small-en-v1.5, local, no API key), and stores them in a
standalone Supabase project. `prior_art_search` embeds your query and ranks by
pgvector cosine similarity. A rolling `patent_assignees` summary powers
`company_patents`.

**Honesty note:** `claims_count` isn't exposed by the PatentsView patent endpoint,
so it's left null (a future enrichment hook); `citation_count` and the rest come
straight from PatentsView.

## Also on

Smithery: `io.github.FoundryNet/patent-intel-mcp`

Built by [FoundryNet](https://foundrynet.io?utm_source=github&utm_medium=readme&utm_campaign=patent-intel-mcp) · forge@foundrynet.io
