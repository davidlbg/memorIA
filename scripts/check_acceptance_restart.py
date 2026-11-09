"""Restart only the named synthetic Compose project and verify durable state."""

import json
import subprocess
import time
from pathlib import Path

import httpx
import psycopg

PREFIX = ["docker", "--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]
PROJECT = "wm-acceptance-20261104"
CONNECTOR = PROJECT + "-whatsapp-connector-1"
HEADERS = {"Authorization": "Bearer acceptance-public-fixture-000000000000000000"}


def docker(*args: str) -> str:
    return subprocess.run(
        PREFIX + list(args), check=True, capture_output=True, text=True, timeout=90
    ).stdout.strip()


def ready() -> None:
    deadline = time.monotonic() + 150
    with httpx.Client(timeout=3) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(
                    "http://127.0.0.1:58002/api/v1/whatsapp/connection", headers=HEADERS
                )
                if response.status_code == 200:
                    status = response.json()
                    if (
                        status["state"] == "DISCONNECTED"
                        and not status["network_enabled"]
                    ):
                        return
            except httpx.TransportError:
                pass
            time.sleep(1)
    raise RuntimeError("Fixture stack failed to recover within 150s")


def counts() -> tuple[int, ...]:
    with (
        psycopg.connect(
            "postgresql://memory:memory@127.0.0.1:58033/memory"
        ) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "SELECT (SELECT count(*) FROM messages), (SELECT count(*) FROM memories), (SELECT count(*) FROM message_embeddings), (SELECT count(*) FROM import_jobs WHERE status='completed')"
        )
        row = cursor.fetchone()
        if row is None or row[0] < 1 or row[3] < 1:
            raise RuntimeError("Synthetic ingestion must complete before restart")
        return tuple(row)


def vault_control() -> str:
    # This volume/key belong only to the synthetic project. Return control
    # fields, never credentials, Signal keys, QR or message text.
    return docker(
        "exec",
        CONNECTOR,
        "node",
        "-e",
        "const f=require('fs'),c=require('crypto'),b=f.readFileSync('/data/session.enc'),d=c.createDecipheriv('aes-256-gcm',f.readFileSync('/run/secrets/whatsapp_key'),b.subarray(4,16));d.setAAD(Buffer.from('WhatsAppMemory/v0.2'));d.setAuthTag(b.subarray(16,32));const v=JSON.parse(Buffer.concat([d.update(b.subarray(32)),d.final()]).toString());console.log(JSON.stringify({instance_id:v.instance_id,pending:v.outbox.length,has_credentials:v.credentials!==null}));",
    )


def main() -> None:
    if (
        docker(
            "inspect",
            CONNECTOR,
            "--format",
            '{{index .Config.Labels "com.docker.compose.project"}}',
        )
        != PROJECT
    ):
        raise RuntimeError("Unexpected fixture ownership")
    docker("start", CONNECTOR)
    ready()
    before, control = counts(), vault_control()
    docker(
        "restart",
        PROJECT + "-db-1",
        PROJECT + "-backend-1",
        PROJECT + "-worker-1",
        CONNECTOR,
    )
    ready()
    if counts() != before or vault_control() != control:
        raise RuntimeError("State differs after graceful restart")
    docker("kill", "--signal", "KILL", CONNECTOR)
    docker("start", CONNECTOR)
    ready()
    if counts() != before or vault_control() != control:
        raise RuntimeError("State differs after abrupt connector restart")
    result = {
        "graceful_restart": "PASS",
        "abrupt_connector_restart": "PASS",
        "counts": before,
        "vault_control_preserved": True,
        "real_session_tested": False,
    }
    Path(".local/acceptance/restart.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
