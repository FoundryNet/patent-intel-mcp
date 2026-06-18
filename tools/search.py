from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def search_patents(
        keyword: Optional[str] = None,
        assignee: Optional[str] = None,
        cpc_code: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        patent_type: Optional[str] = None,
        limit: int = 25,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Search U.S. patents (USPTO PatentsView) by keyword, assignee, CPC class,
        date range, or type. Patent search for IP and technology-landscape research,
        sorted newest-first, with title, abstract, assignee, and CPC codes.

        PAID: $0.01 USDC per query after a daily free allowance (25/day). On a 402,
        pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. Pass agent_id to scope your allowance; an
        Authorization: Bearer fnet_ key bypasses the paywall.

        Args:
            keyword: free-text matched against title + abstract.
            assignee: assignee/company name, partial match.
            cpc_code: CPC class/subclass prefix, e.g. "H04L" or "G06N".
            date_from: ISO date "YYYY-MM-DD"; grant_date on/after.
            date_to: ISO date "YYYY-MM-DD"; grant_date on/before.
            patent_type: "utility", "design", or "plant".
            limit: max rows (1-100, default 25).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        filters = {"keyword": keyword, "assignee": assignee, "cpc_code": cpc_code,
                   "date_from": date_from, "date_to": date_to,
                   "patent_type": patent_type, "limit": limit}
        return await core.do_search(filters, agent_key=identity.resolve_agent_key(agent_id),
                                    payment_tx=payment_tx, api_key=identity.bearer())
