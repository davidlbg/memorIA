from fastapi import HTTPException, Request

from app.config import get_settings


def token_for_path(path: str) -> str:
    settings = get_settings()
    if path.startswith("/internal/whatsapp/"):
        return settings.connector_secret.get_secret_value()
    return settings.auth_secret.get_secret_value()


def require_internal(request: Request) -> None:
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "A API interna aceita somente transporte local")
