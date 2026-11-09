"""Restore a local dump into a new isolated database; never overwrite an installation."""

import argparse
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4


def restore_error_category(stderr: bytes) -> str:
    """Return only a fixed category; SQL/data must never enter terminal output."""
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        match = re.match(r"^(?:pg_restore: error: .*?ERROR:\s*|ERROR:\s*)(.*)", line)
        if not match:
            if (
                line.startswith("pg_restore: error:")
                and "server closed the connection unexpectedly" in line
            ):
                return "connection_interrupted"
            continue
        reason = match.group(1).casefold()
        if reason.startswith(
            (
                "could not resize shared memory segment",
                "could not create shared memory segment",
            )
        ):
            return "shared_memory_limit"
        if reason.startswith("out of memory"):
            return "memory_limit"
        if reason.startswith("unrecognized configuration parameter"):
            return "server_version_mismatch"
        if reason.startswith("could not open extension control file"):
            return "missing_extension"
        if reason.startswith("could not access file"):
            return "missing_server_library"
        if reason.startswith("invalid input syntax"):
            return "invalid_backup_data"
        if reason.startswith("duplicate key value violates"):
            return "duplicate_data_or_nonempty_target"
        if (
            reason.startswith("could not write to file")
            and "no space left on device" in reason
        ):
            return "storage_limit"
    return "UNKNOWN_restore_error"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path)
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Preserve the restored isolated database for local inspection",
    )
    args = parser.parse_args()
    source = args.dump.resolve(strict=True)
    if not source.is_file() or source.suffix != ".dump":
        parser.error("Choose a local pg_dump custom-format .dump file")
    # Force a local daemon. This command must never restore into a remote context.
    endpoint = (
        "npipe:////./pipe/dockerDesktopLinuxEngine"
        if os.name == "nt"
        else "unix:///var/run/docker.sock"
    )
    docker = ["docker", "--host", endpoint]
    name = "wm-restore-" + uuid4().hex
    subprocess.run(
        [
            *docker,
            "run",
            "--detach",
            "--name",
            name,
            "--network",
            "none",
            "--env",
            "POSTGRES_DB=memory_restore_test",
            "--env",
            "POSTGRES_USER=memory",
            "--env",
            "POSTGRES_PASSWORD=restore-isolated-fixture",
            "pgvector/pgvector:0.8.1-pg17",
        ],
        check=True,
        capture_output=True,
    )
    succeeded = False
    try:
        for attempt in range(60):
            ready = subprocess.run(
                [
                    *docker,
                    "exec",
                    name,
                    "pg_isready",
                    "-h",
                    "127.0.0.1",
                    "-U",
                    "memory",
                    "-d",
                    "memory_restore_test",
                ],
                check=False,
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            if attempt == 59:
                raise RuntimeError("Isolated restore database did not start")
            time.sleep(1)
        with source.open("rb") as data:
            restored = subprocess.run(
                [
                    *docker,
                    "exec",
                    "-i",
                    name,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "-U",
                    "memory",
                    "-d",
                    "memory_restore_test",
                ],
                stdin=data,
                check=False,
                capture_output=True,
            )
        if restored.returncode:
            raise RuntimeError(
                f"Restore failed ({restore_error_category(restored.stderr)}); no existing database was changed. SQL/data diagnostics remain private."
            )
        verified = subprocess.run(
            [
                *docker,
                "exec",
                name,
                "psql",
                "-U",
                "memory",
                "-d",
                "memory_restore_test",
                "-Atc",
                "SELECT version_num FROM alembic_version; SELECT count(*) FROM conversations; SELECT count(*) FROM messages; SELECT count(*) FROM memories;",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if verified.returncode:
            raise RuntimeError(
                "Restored database is missing the expected application schema"
            )
        print("Restore succeeded in a new offline database. Revision and row counts:")
        print(verified.stdout.strip())
        if args.keep:
            print("Preserved isolated container: " + name)
        succeeded = True
    finally:
        if not args.keep or not succeeded:
            # Removes only the UUID-named container created by this invocation
            # and its own anonymous volume. No installation volumes are mounted.
            subprocess.run(
                [*docker, "rm", "--force", "--volumes", name],
                check=True,
                capture_output=True,
            )


if __name__ == "__main__":
    main()
