"""Operate the isolated personal installation without printing credentials or conversations."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ["docker"]
if os.name == "nt":
    DOCKER += ["--host", "npipe:////./pipe/dockerDesktopLinuxEngine"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", type=str.lower, choices=["start", "stop", "status"])
    args = parser.parse_args()
    compose = [
        *DOCKER,
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "infrastructure/personal.compose.yml",
        "--profile",
        "whatsapp",
    ]
    if args.action == "start":
        subprocess.run([sys.executable, "scripts/prepare_personal.py"], cwd=ROOT, check=True)
        # Release the lease while the old backend is still available. Recreating
        # the backend first strands an unexpired lease and makes an upgrade
        # temporarily fail its connector readiness check.
        subprocess.run(
            [*compose, "stop", "--timeout", "30", "whatsapp-connector"], cwd=ROOT, check=True
        )
        subprocess.run(
            [*compose, "up", "-d", "--no-build", "--wait", "--wait-timeout", "180", "backend", "worker", "indexer"],
            cwd=ROOT,
            check=True,
        )
        # The connector shares the backend network namespace. Recreate it only
        # after the backend is healthy, including after backend replacement.
        subprocess.run(
            [*compose, "up", "-d", "--no-build", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "180", "whatsapp-connector"],
            cwd=ROOT, check=True,
        )
        print(
            "Personal API: http://127.0.0.1:58003. Pair through the extension; never share the QR or token."
        )
    elif args.action == "stop":
        # Stop preserves the database, encrypted session, key and downloaded models.
        subprocess.run([*compose, "stop", "--timeout", "30", "whatsapp-connector"], cwd=ROOT, check=True)
        subprocess.run([*compose, "stop", "--timeout", "180"], cwd=ROOT, check=True)
    else:
        subprocess.run([*compose, "ps", "--all"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
