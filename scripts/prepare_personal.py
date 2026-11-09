"""Prepare private local configuration without reading existing authentication secrets."""

import csv
import os
import re
import secrets
import subprocess
from pathlib import Path


def private_file(path: Path, content: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print(f"Preserved {path.name} without reading it")
    else:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
    if os.name == "nt":
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
        )
        sid = next(csv.reader(result.stdout.splitlines()))[1]
        if not re.fullmatch(r"S-\d+(?:-\d+)+", sid):
            raise RuntimeError(
                "Cannot determine the owner SID for private configuration"
            )
        acl = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"*{sid}:F",
                "*S-1-5-18:F",
            ],
            capture_output=True,
            check=False,
        )
        if acl.returncode:
            raise RuntimeError(
                "Could not protect local configuration; inspect file ACLs before continuing"
            )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / ".local" / "personal"
    target.mkdir(parents=True, exist_ok=True)
    if (target / "auth.env").exists() and not (target / "whatsapp.key").exists():
        raise RuntimeError(
            "Existing configuration has no vault key; recover the original key before continuing"
        )
    private_file(target / "ai.key", secrets.token_bytes(32))
    private_file(target / "whatsapp.key", secrets.token_bytes(32))
    private_file(
        target / "auth.env",
        (
            f"AUTH_SECRET={secrets.token_urlsafe(48)}\nCONNECTOR_SECRET={secrets.token_urlsafe(48)}\n"
        ).encode(),
    )
    private_file(
        target / "runtime.env",
        (
            b"LLM_PROVIDER=mock\nEMBEDDING_PROVIDER=mock\nLOCAL_AI_ENABLED=false\n"
            b"EXTERNAL_AI_ENABLED=false\nAI_BACKGROUND_ENABLED=false\nOPENAI_API_KEY=\n"
            b"LLM_MODEL=gpt-4.1-mini\nEMBEDDING_MODEL=text-embedding-3-small\nEMBEDDING_DIMENSIONS=1024\n"
            b"PROVIDER_TIMEOUT_SECONDS=120\nWORKER_BATCH_MESSAGES=1\n"
            b"CONNECTOR_NETWORK_ENABLED=true\nCONNECTOR_MAX_QUEUE=25000\n"
        ),
    )
    print(
        "Private environment prepared. WhatsApp network authorized, pairing remains an explicit operator action. No credentials were displayed."
    )


if __name__ == "__main__":
    main()
