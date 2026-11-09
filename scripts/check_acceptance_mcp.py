"""Compare synthetic evidence over REST and MCP in the isolated acceptance stack."""

import asyncio

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from app.schemas.domain import SourceMessage

URL = "http://127.0.0.1:58002"
HEADERS = {"Authorization": "Bearer acceptance-public-fixture-000000000000000000"}


async def main() -> None:
    async with httpx.AsyncClient() as http:
        response = await http.get(URL + "/api/v1/messages/search", headers=HEADERS)
        response.raise_for_status()
        sources = [SourceMessage.model_validate(item) for item in response.json()]
        fixtures = [
            item
            for item in sources
            if (item.external_message_id or "").startswith("ACCEPTANCE")
        ]
        if not fixtures:
            raise RuntimeError("Run the synthetic connector acceptance test first")
        source = fixtures[0]
    async with (
        streamablehttp_client(URL + "/mcp/", headers=HEADERS) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            "search_messages", {"query": source.content, "limit": 20}
        )
        if result.isError:
            raise RuntimeError("MCP returned an error")
        encoded = result.model_dump_json()
        if str(source.id) not in encoded or source.content not in encoded:
            raise RuntimeError("MCP evidence differs from REST")
        print(
            "PASS: authenticated REST and MCP return the same synthetic source ID and literal text"
        )


if __name__ == "__main__":
    asyncio.run(main())
