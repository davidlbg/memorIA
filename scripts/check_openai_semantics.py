"""Exercise the real OpenAI API against a dedicated synthetic database, never a user account."""

import asyncio
import json
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = "http://127.0.0.1:58004"
HEADERS = {"Authorization": "Bearer semantic-public-fixture-00000000000000000000"}
TEXT = (
    "05/11/2026, 10:00 - Alice: O projeto Aurora precisa de alguém para revisar o contrato. Você consegue?\n"
    "05/11/2026, 10:01 - Bruno: Sim, deixo tudo conferido e devolvo até sexta-feira.\n"
    "05/11/2026, 10:02 - Alice: Obrigada! O lançamento permanece na próxima segunda-feira.\n"
    "05/11/2026, 10:03 - Bruno: Talvez eu consiga ir ao escritório amanhã, ainda não confirmei.\n"
)


async def main() -> None:
    if (
        URL != "http://127.0.0.1:58004"
        or HEADERS["Authorization"]
        != "Bearer semantic-public-fixture-00000000000000000000"
    ):
        raise RuntimeError("Fresh acceptance may reset only the isolated synthetic API")
    started = time.monotonic()
    checkpoint: dict[str, object] = {}
    failures: list[str] = []

    def check(condition: bool, description: str) -> None:
        print(("PASS: " if condition else "FAIL: ") + description, flush=True)
        if not condition:
            failures.append(description)

    target = (
        Path(__file__).resolve().parents[1] / ".local" / "openai-semantic" / "checkpoint.json"
    )

    def record(stage: str, value: object) -> None:
        checkpoint[stage] = value
        target.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async with httpx.AsyncClient(base_url=URL, headers=HEADERS, timeout=660) as client:
        health = await client.get("/api/v1/health")
        health.raise_for_status()
        assert health.json()["llm_provider"] == "openai"
        assert health.json()["embedding_provider"] == "openai"
        assert health.json()["external_ai_enabled"]
        assert not health.json()["local_ai_enabled"]
        record("health", health.json())
        file = {
            "file": (
                "semantic-synthetic-local-profile.txt",
                TEXT.encode(),
                "text/plain",
            )
        }
        data = {"date_order": "DMY", "timezone": "America/Sao_Paulo"}
        preview = await client.post("/api/v1/imports/preview", files=file, data=data)
        preview.raise_for_status()
        jobs = await client.get("/api/v1/imports")
        jobs.raise_for_status()
        # Always exercise fresh extraction. Reusing completed memories could
        # incorrectly credit a new model or contract with an old model's output.
        fixture_ids = {
            item["conversation_id"]
            for item in jobs.json()
            if item["filename"] == "semantic-synthetic-local-profile.txt"
        }
        if fixture_ids:
            catalog = await client.get("/api/v1/conversations", params={"limit": 100})
            catalog.raise_for_status()
            titles = {item["id"]: item["title"] for item in catalog.json()}
            for fixture_id in fixture_ids:
                if titles.get(fixture_id) != "SINTETICO — Projeto Aurora":
                    raise RuntimeError(
                        "Refusing to reset an unidentified fixture conversation"
                    )
                removed = await client.delete("/api/v1/conversations/" + fixture_id)
                removed.raise_for_status()
        imported = await client.post(
            "/api/v1/imports",
            files=file,
            data={
                **data,
                "title": "SINTETICO — Projeto Aurora",
                "preview_hash": preview.json()["file_hash"],
            },
        )
        imported.raise_for_status()
        job = imported.json()
        conversation = job["conversation_id"]
        print(
            "Imported synthetic dialogue; waiting for real OpenAI extraction", flush=True
        )
        deadline = time.monotonic() + 2400
        last_progress = None
        while True:
            response = await client.get("/api/v1/imports/" + job["id"])
            response.raise_for_status()
            job = response.json()
            progress = (
                job["status"],
                job["processed_messages"],
                job["total_messages"],
                job["attempts"],
            )
            if progress != last_progress:
                print(
                    f"Extraction: {progress[0]} {progress[1]}/{progress[2]}, attempts={progress[3]}",
                    flush=True,
                )
                last_progress = progress
            if job["status"] == "completed":
                break
            if job["status"] == "failed" or time.monotonic() > deadline:
                break
            await asyncio.sleep(3)
        record("job", job)
        check(
            job["status"] == "completed"
            and job["processed_messages"] == 4
            and job["imported_messages"] == 4,
            "OpenAI extraction processes all four originals",
        )
        memories = await client.get(
            "/api/v1/memories", params={"conversation_id": conversation}
        )
        memories.raise_for_status()
        record("memories", memories.json())
        print("Extraction completed; memories=" + str(len(memories.json())), flush=True)
        check(
            any(
                memory["memory_type"] == "commitment"
                and memory["evidence_basis"] == "interpretation"
                and any(
                    word in memory["content"].casefold()
                    for word in ("aurora", "contrato", "revis")
                )
                and any(
                    "Sim, deixo" in source["content"] for source in memory["sources"]
                )
                and any(
                    "Aurora" in source["content"]
                    for source in memory["sources"] + memory.get("context_sources", [])
                )
                for memory in memories.json()
            ),
            "Contextual commitment names its object with traceable question and reply",
        )
        tentative = [
            memory
            for memory in memories.json()
            if any("Talvez" in source["content"] for source in memory["sources"])
        ]
        check(
            bool(tentative)
            and all(
                memory["memory_type"] == "intention"
                and any(
                    word in memory["content"].casefold()
                    for word in (
                        "talvez",
                        "possib",
                        "possív",
                        "poderia",
                        "considera",
                        "cogita",
                        "não confirm",
                        "ainda não",
                        "incert",
                    )
                )
                for memory in tentative
            ),
            "Tentative office visit is extracted without losing uncertainty",
        )
        check(
            not any(
                memory["sources"]
                and all(
                    "Aurora precisa" in source["content"]
                    for source in memory["sources"]
                )
                and (
                    memory["memory_type"] == "commitment"
                    or (
                        memory["memory_type"] == "intention"
                        and "bruno" in memory["content"].casefold()
                    )
                )
                for memory in memories.json()
            ),
            "A request alone does not invent recipient acceptance",
        )
        question = (
            "Quem assumiu a checagem do documento jurídico e qual o prazo combinado?"
        )
        answer = await client.post(
            "/api/v1/assistant/query",
            json={"question": question, "conversation_id": conversation},
        )
        result = answer.json()
        record("answer", result)
        check(answer.status_code == 200, "Positive query returns a successful response")
        if answer.status_code == 200:
            check(
                not result["insufficient_evidence"] and bool(result["claims"]),
                "Positive query has evidence",
            )
            check(
                all(source["content"] in TEXT for source in result["sources"]),
                "Every returned citation is an original",
            )
            check(
                any("sexta" in claim["text"].casefold() for claim in result["claims"]),
                "Paraphrase resolves deadline",
            )
            check(
                any("bruno" in claim["text"].casefold() for claim in result["claims"]),
                "Paraphrase resolves responsible person",
            )
        if answer.status_code == 200:
            required = {
                source["id"]
                for source in result["sources"] + result.get("context_sources", [])
                if "Aurora precisa" in source["content"]
                or "Sim, deixo" in source["content"]
            }
            check(
                len(required) == 2
                and any(
                    required.issubset(set(claim["source_message_ids"]))
                    for claim in result["claims"]
                ),
                "Answer cites both the request identifying the object and the acceptance",
            )
        negative = await client.post(
            "/api/v1/assistant/query",
            json={
                "question": "Qual é o valor em reais do contrato do projeto Aurora?",
                "conversation_id": conversation,
            },
        )
        record("negative", negative.json())
        check(
            negative.status_code == 200, "Negative query returns a successful response"
        )
        if negative.status_code == 200:
            check(
                negative.json()["insufficient_evidence"]
                and not negative.json()["claims"],
                "Missing contract value returns insufficient evidence",
            )
            check(
                bool(negative.json().get("context_sources")),
                "Negative query refuses despite retrieved dialogue",
            )
        async with (
            streamablehttp_client(URL + "/mcp/", headers=HEADERS) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            evidence = await session.call_tool(
                "search_messages",
                {"query": "Aurora", "conversation_id": conversation, "limit": 10},
            )
            check(
                not evidence.isError and "Aurora" in evidence.model_dump_json(),
                "Authenticated MCP exposes the same synthetic originals",
            )
        output = (
            Path(__file__).resolve().parents[1] / ".local" / "openai-semantic" / "result.json"
        )
        output.write_text(
            json.dumps(
                {
                    "status": "failed" if failures else "passed",
                    "failures": failures,
                    "health": health.json(),
                    "elapsed_seconds": round(time.monotonic() - started),
                    "memories": memories.json(),
                    "answer": result,
                    "negative": negative.json(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("Synthetic proof saved to .local/openai-semantic/result.json", flush=True)
        if failures:
            raise AssertionError(
                "OpenAI semantic acceptance failed: " + "; ".join(failures)
            )


if __name__ == "__main__":
    asyncio.run(main())
