import hmac
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.routes.v1 import router
from app.api.routes.whatsapp import internal, public
from app.config import get_settings
from app.database.session import engine
from app.integrations.errors import BudgetConfigurationError, BudgetExceeded
from app.integrations.providers import ProviderError
from app.mcp.server import create_mcp
from app.security.body_limit import BodyLimitMiddleware
from app.security.internal import token_for_path


def create_app() -> FastAPI:
    configured = get_settings()
    if configured.connector_secret.get_secret_value() and (
        len(configured.connector_secret.get_secret_value()) < 32
        or configured.connector_secret.get_secret_value()
        == configured.auth_secret.get_secret_value()
    ):
        raise RuntimeError("CONNECTOR_SECRET must be unique and at least 32 characters")
    mcp = create_mcp()
    mcp_app = mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = get_settings()
        if len(settings.auth_secret.get_secret_value()) < 32:
            raise RuntimeError(
                "Configure AUTH_SECRET with at least 32 characters; see scripts/setup.py"
            )
        with engine().connect() as connection:
            rows: Sequence[str] = (
                connection.execute(
                    text(
                        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid IN ('message_embeddings'::regclass, 'memory_embeddings'::regclass) AND attname='embedding'"
                    )
                )
                .scalars()
                .all()
            )
            if len(rows) != 2 or any(
                row != f"vector({settings.embedding_dimensions})" for row in rows
            ):
                raise RuntimeError(
                    "Embedding dimensions differ from database; create a planned schema migration"
                )
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="WhatsApp Memory", version="0.2.0", lifespan=lifespan)
    origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    if get_settings().extension_origin:
        origins.append(get_settings().extension_origin)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "DELETE", "PATCH"],
        allow_headers=["Authorization", "Content-Type"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def protect(request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Authentication runs before multipart parsing and before the mounted MCP app.
        # CORS preflights contain no private data and are handled by CORS middleware.
        if request.method != "OPTIONS":
            token = token_for_path(request.url.path)
            if not token or not hmac.compare_digest(
                request.headers.get("authorization", "").encode(), f"Bearer {token}".encode()
            ):
                return JSONResponse(
                    {"error": {"code": 401, "message": "Autenticação necessária"}},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
        declared_length = request.headers.get("content-length")
        if declared_length:
            try:
                too_large = int(declared_length) > get_settings().import_max_bytes + 65536
            except ValueError:
                return JSONResponse(
                    {"error": {"code": 400, "message": "Content-Length inválido"}}, status_code=400
                )
            if too_large:
                return JSONResponse(
                    {"error": {"code": 413, "message": "Upload excede o limite"}}, status_code=413
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": exc.status_code, "message": str(exc.detail)}},
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo rejected inputs; they can include private messages/tokens.
        return JSONResponse(
            {
                "error": {
                    "code": 422,
                    "message": "Parâmetros inválidos",
                    "fields": [
                        ".".join(str(part) for part in error["loc"]) for error in exc.errors()
                    ],
                }
            },
            status_code=422,
        )

    @app.exception_handler(BudgetExceeded)
    async def budget_error(request: Request, exc: BudgetExceeded) -> JSONResponse:
        return JSONResponse({"error": {"code": 402, "message": "O orçamento mensal compartilhado não tem margem para esta chamada. Aguarde consultas em andamento ou revise o consumo em Ajustes. A captura do WhatsApp continua."}}, status_code=402)

    @app.exception_handler(BudgetConfigurationError)
    async def budget_configuration_error(request: Request, exc: BudgetConfigurationError) -> JSONResponse:
        return JSONResponse({"error": {"code": 503, "message": "Não foi possível validar a contabilização de custos. A chamada paga foi bloqueada; verifique a configuração do orçamento."}}, status_code=503)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        # Only fixed, allowlisted diagnostics may reach the client. Never expose
        # arbitrary exception text, upstream bodies, credentials or SQL inputs.
        safe_messages = {
            "Provider billing unconfirmed; automatic retry disabled": "Não foi possível confirmar o custo desta chamada. Ela não foi repetida automaticamente; a reserva de segurança aparece em Ajustes.",
            "OpenAI: chave inválida, revogada ou sem permissão para o recurso": "OpenAI: chave inválida, revogada ou sem permissão. Verifique a chave em Ajustes.",
            "OpenAI: saldo ou cota do projeto insuficiente; verifique o faturamento": "OpenAI: saldo ou cota do projeto insuficiente. Verifique o faturamento da API.",
        }
        return JSONResponse(
            {
                "error": {
                    "code": 503,
                    "message": safe_messages.get(str(exc), "IA indisponível ou evidência inválida. Mensagens importadas foram preservadas."),
                }
            },
            status_code=503,
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": 503,
                    "message": "Banco temporariamente indisponível. Tente novamente.",
                }
            },
            status_code=503,
        )

    app.include_router(router)
    app.include_router(internal)
    app.include_router(public)
    app.mount("/mcp", mcp_app)
    app.add_middleware(BodyLimitMiddleware)

    return app


app = create_app()
