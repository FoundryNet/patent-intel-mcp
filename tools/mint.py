import core


def register(mcp) -> None:
    @mcp.tool
    async def mint_info() -> dict:
        """FoundryNet Data Network info. FREE.

        Returns provenance/attestation details for this server's results and the
        sister data servers (gov-contracts-mcp, brand-intel-mcp).
        """
        return core.mint_info()
