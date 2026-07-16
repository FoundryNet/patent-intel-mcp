from typing import Optional

import core
import identity


def register(mcp) -> None:
    @mcp.tool
    async def prior_art_search(
        description: str,
        max_results: Optional[int] = 10,
        agent_id: Optional[str] = None,
        payment_tx: Optional[str] = None,
    ) -> dict:
        """Prior-art search: find patents semantically similar to a free-text
        invention description, using pgvector cosine similarity over patent abstract
        embeddings. The premium IP-research tool.

        PAID: $0.02 per query after the daily free allowance (25/day). On a
        402, settle the returned payment challenge and re-call with the SAME args
        plus payment_tx=<reference>. An Authorization: Bearer fnet_ key bypasses it.

        Args:
            description: free-text description of the invention / claim to match.
            max_results: number of similar patents to return (1-50, default 10).
            agent_id: stable id for your agent (scopes the free-tier counter).
            payment_tx: payment transaction reference, when re-calling after a 402.
        """
        return await core.do_prior_art(description, max_results,
                                       agent_key=identity.resolve_agent_key(agent_id),
                                       payment_tx=payment_tx, api_key=identity.bearer())
