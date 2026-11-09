import hmac

from fastapi import HTTPException, Request

from app.config import get_settings


def authenticated(request: Request) -> None:
    expected = get_settings().auth_secret.get_secret_value()
    provided = request.headers.get("authorization", "")
    if not expected or not hmac.compare_digest(provided.encode(), f"Bearer {expected}".encode()):
        raise HTTPException(401, "Autenticação necessária", headers={"WWW-Authenticate": "Bearer"})
