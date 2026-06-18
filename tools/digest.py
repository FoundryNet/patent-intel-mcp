from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def daily_digest(
        cpc_code: Optional[str] = None,
        assignee: Optional[str] = None,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Structured daily patent-filing digest — what published/granted in the
        last day, with top CPC classes, top assignees, and the patent list.
        Optionally scoped to a CPC class or an assignee.

        PAID: $0.02 USDC per query after the daily free allowance (25/day). On a
        402, pay the returned Solana memo and re-call with the SAME args plus
        payment_tx=<signature>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            cpc_code: optional CPC class/subclass prefix to scope the digest.
            assignee: optional assignee/company name to scope the digest.
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: Solana tx signature, when re-calling after a 402.
        """
        return await core.do_digest(cpc_code, assignee,
                                    agent_key=identity.resolve_agent_key(agent_id),
                                    payment_tx=payment_tx, api_key=identity.bearer())
