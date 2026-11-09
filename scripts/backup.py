"""Back up this local Compose database. No provider or remote database access."""

import argparse
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--personal", action="store_true", help="Back up the isolated personal database"
)
args = parser.parse_args()
endpoint = (
    "npipe:////./pipe/dockerDesktopLinuxEngine"
    if os.name == "nt"
    else "unix:///var/run/docker.sock"
)
compose = ["docker", "--host", endpoint, "compose"]
if args.personal:
    compose += ["-f", "docker-compose.yml", "-f", "infrastructure/personal.compose.yml"]
folder = root / ".local" / "backups"
folder.mkdir(parents=True, exist_ok=True)
target = folder / f"memory-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.dump"
try:
    with target.open("xb") as output:
        subprocess.run(
            [
                *compose,
                "exec",
                "-T",
                "db",
                "pg_dump",
                "-U",
                "memory",
                "-d",
                "memory",
                "-Fc",
            ],
            cwd=root,
            stdout=output,
            check=True,
        )
except (subprocess.CalledProcessError, OSError):
    target.unlink(missing_ok=True)
    raise SystemExit("Local backup failed; partial output removed.") from None
print(f"Local backup created: {target}")
