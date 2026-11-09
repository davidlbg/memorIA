"""Create local configuration without reading or printing existing credentials."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".env"
template = (root / ".env.example").read_text(encoding="utf-8")
try:
    with target.open("x", encoding="utf-8") as file:
        file.write(
            template.replace(
                "AUTH_SECRET=\n", f"AUTH_SECRET={secrets.token_urlsafe(48)}\n"
            )
        )
except FileExistsError:
    print(".env already exists; preserved without reading it.")
else:
    print(
        "Created .env with a random local authentication token. Keep this file private."
    )

connector_config = root / ".env.connector"
key_path = root / ".local" / "secrets" / "whatsapp.key"
key_path.parent.mkdir(parents=True, exist_ok=True)
try:
    descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    print("Connector encryption key exists; preserved without reading it.")
else:
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(secrets.token_bytes(32))
try:
    descriptor = os.open(connector_config, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    print(".env.connector exists; preserved without reading it.")
else:
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(
            f"CONNECTOR_SECRET={secrets.token_urlsafe(48)}\nCONNECTOR_KEY_FILE=.local/secrets/whatsapp.key\nCONNECTOR_NETWORK_ENABLED=false\nCONNECTOR_BACKEND_URL=http://127.0.0.1:8000\nCONNECTOR_VAULT_FILE=.local/whatsapp/session.enc\n"
        )
    print(
        "Created .env.connector and separate encryption key. External WhatsApp connection remains disabled."
    )

# Separate encryption key for provider credentials configured from the extension.
ai_key_path = root / ".local" / "secrets" / "ai.key"
try:
    descriptor = os.open(ai_key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    print("AI encryption key exists; preserved without reading it.")
else:
    with os.fdopen(descriptor, "wb") as ai_file:
        ai_file.write(secrets.token_bytes(32))
