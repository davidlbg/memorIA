import asyncio
import socket
from threading import Thread

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.core.ingestion.parser import parse_export
from app.services.imports import commit_import

pytestmark = pytest.mark.database


async def test_external_mcp_client_authenticated_round_trip(db: sessionmaker[Session]) -> None:
    from app.main import create_app

    with db.begin() as session:
        commit_import(
            session,
            parse_export(
                b"31/10/2026, 10:00 - Alice: I promise to send the document.", "DMY", "UTC"
            ),
            "synthetic.txt",
            "Synthetic MCP",
            None,
        )
    with socket.socket() as bound:
        bound.bind(("127.0.0.1", 0))
        port = bound.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(), host="127.0.0.1", port=port, log_level="critical", access_log=False
            )
        )
        thread = Thread(target=lambda: server.run(sockets=[bound]), daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.05)
            assert server.started
            url = f"http://127.0.0.1:{port}/mcp/"
            async with httpx.AsyncClient() as http:
                assert (await http.post(url, json={})).status_code == 401
            async with streamablehttp_client(
                url,
                headers={
                    "Authorization": f"Bearer {get_settings().auth_secret.get_secret_value()}"
                },
            ) as (read, write, _):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    assert len(tools.tools) == 5
                    result = await client.call_tool(
                        "search_messages", {"query": "document", "limit": 5}
                    )
                    assert not result.isError
                    assert "I promise to send the document." in result.model_dump_json()
                    rejected = await client.call_tool(
                        "search_messages", {"query": "document", "limit": 10000}
                    )
                    assert rejected.isError
        finally:
            server.should_exit = True
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()
