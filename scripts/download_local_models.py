"""Download only public model weights, before introducing personal conversation data."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ollama/ollama@sha256:bdf98cbc256dd2152d074bee07dc788745026eaa8a8667498a9a5cf237772ea4"
DOCKER = [
    "docker",
    "--host",
    "npipe:////./pipe/dockerDesktopLinuxEngine"
    if os.name == "nt"
    else "unix:///var/run/docker.sock",
]
MODELS = ("qwen3.5:2b", "qwen3-embedding:0.6b")


def main() -> None:
    expected = json.loads(
        (ROOT / "infrastructure" / "local-models.json").read_text(encoding="utf-8")
    )
    if (
        not isinstance(expected, dict)
        or set(expected) != set(MODELS)
        or any(
            not isinstance(value, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
            for value in expected.values()
        )
    ):
        raise RuntimeError("Invalid local model lockfile")
    container = f"wm-model-download-{os.getpid()}"
    subprocess.run(
        [*DOCKER, "volume", "create", "wm_local_models"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            *DOCKER,
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            "bridge",
            "--env",
            "OLLAMA_NO_CLOUD=1",
            "--volume",
            "wm_local_models:/root/.ollama",
            IMAGE,
        ],
        check=True,
        capture_output=True,
    )
    try:
        for attempt in range(30):
            ready = subprocess.run(
                [*DOCKER, "exec", container, "ollama", "list"],
                capture_output=True,
                check=False,
            )
            if ready.returncode == 0:
                break
            if attempt == 29:
                raise RuntimeError("Model-download service did not start")
            time.sleep(1)
        for model in MODELS:
            subprocess.run(
                [*DOCKER, "exec", container, "ollama", "pull", model], check=True
            )
        # Query the runtime's identity contract, rather than guessing it from
        # manifest serialization. No host port is published by either container.
        response = subprocess.run(
            [
                *DOCKER,
                "run",
                "--rm",
                "--network",
                f"container:{container}",
                "--entrypoint",
                "python",
                "python:3.12-slim",
                "-c",
                "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=10).read(2000000).decode())",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        installed = json.loads(response.stdout)["models"]
        inventory = {
            model["name"]: "sha256:" + model["digest"].removeprefix("sha256:")
            for model in installed
            if model["name"] in MODELS
        }
        if set(inventory) != set(MODELS):
            raise RuntimeError("The local model inventory is incomplete")
        if inventory != expected:
            raise RuntimeError(
                "A model tag changed; refusing to activate weights that differ from the project lockfile"
            )
        target = ROOT / ".local" / "local-models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
        print(
            "Public models installed and pinned. No conversations or credentials were accessed."
        )
    finally:
        # Only this transient download container is removed. Model data remains.
        subprocess.run(
            [*DOCKER, "rm", "--force", container], check=True, capture_output=True
        )


if __name__ == "__main__":
    main()
