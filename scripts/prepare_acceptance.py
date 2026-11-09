"""Create isolated synthetic acceptance configuration; never read application secrets."""

from pathlib import Path

root = Path(__file__).resolve().parents[1] / ".local" / "acceptance"
root.mkdir(parents=True, exist_ok=True)
fixtures = {
    "fixture.env": (
        b"AUTH_SECRET=acceptance-public-fixture-000000000000000000\n"
        b"CONNECTOR_SECRET=acceptance-internal-fixture-0000000000000000\n"
        b"CONNECTOR_NETWORK_ENABLED=false\nLLM_PROVIDER=mock\n"
        b"EMBEDDING_PROVIDER=mock\nEXTERNAL_AI_ENABLED=false\n"
    ),
    "fixture.key": bytes(range(32)),
}
for name, content in fixtures.items():
    try:
        with (root / name).open("xb") as file:
            file.write(content)
    except FileExistsError:
        print(f"Preserved existing acceptance fixture: {name}")
print(
    "Synthetic fixtures only. Never use these credentials for personal data or real pairing."
)
