from datetime import timedelta
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text

from app.api.routes.v1 import DB
from app.config import get_settings
from app.models.entities import Message, WhatsAppConnection, now
from app.schemas.whatsapp import (
    ClaimLease,
    ConnectionUpdate,
    DeliveryResult,
    EventBatch,
    Lease,
    PublicConnectorStatus,
    SyncCheckpoint,
)
from app.security.auth import authenticated
from app.security.internal import require_internal
from app.services.whatsapp import LEASE_SECONDS, claim, ingest_events, require_lease, update_status

internal = APIRouter(prefix="/internal/whatsapp", dependencies=[Depends(require_internal)])
public = APIRouter(prefix="/api/v1/whatsapp", dependencies=[Depends(authenticated)])


@internal.post("/lease", response_model=Lease)
def acquire_lease(body: ClaimLease, session: DB) -> Lease:
    return claim(session, body)


@internal.post("/lease/heartbeat", response_model=Lease)
def heartbeat(body: Lease, session: DB) -> Lease:
    row = require_lease(session, body)
    row.lease_expires_at = now() + timedelta(seconds=LEASE_SECONDS)
    return body


@internal.post("/lease/release")
def release(body: Lease, session: DB) -> dict[str, bool]:
    row = require_lease(session, body)
    row.lease_expires_at = now()
    row.state = "DISCONNECTED"
    return {"released": True}


@internal.post("/connection/status")
def status(body: ConnectionUpdate, session: DB) -> dict[str, bool]:
    update_status(session, body)
    return {"accepted": True}


@internal.get("/connection")
def persisted_status(session: DB) -> dict[str, object]:
    row = session.scalar(select(WhatsAppConnection).where(WhatsAppConnection.slot == "local"))
    return {
        "state": row.state if row and row.lease_expires_at > now() else "DISCONNECTED",
        "last_sequence": row.last_sequence if row else 0,
        "history_complete": False,
    }


@internal.post("/events", response_model=DeliveryResult)
def events(body: EventBatch, session: DB) -> DeliveryResult:
    session.execute(text("SELECT set_config('lock_timeout','10s',true)"))
    return ingest_events(session, body)


@internal.post("/sync/checkpoint")
def checkpoint(body: SyncCheckpoint, session: DB) -> dict[str, int]:
    row = require_lease(session, body)
    if body.sequence > row.last_sequence:
        raise HTTPException(409, "Checkpoint não confirmado pela ingestão")
    return {"last_sequence": row.last_sequence}


async def connector_request(method: str, path: str, session: DB) -> PublicConnectorStatus:
    settings = get_settings()
    origin = urlparse(settings.connector_url)
    if (
        origin.scheme != "http"
        or origin.hostname not in ("127.0.0.1", "localhost")
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
    ):
        raise HTTPException(503, "Configure um endereço loopback para o conector")
    secret = settings.connector_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(503, "Conector não configurado; execute o setup da v0.2")
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.request(
                method,
                settings.connector_url.rstrip("/") + path,
                headers={"Authorization": f"Bearer {secret}"},
            )
        if response.status_code >= 400:
            if response.status_code == 409:
                detail = response.json()
                if isinstance(detail, dict) and detail.get("error_code") == "OUTBOX_DRAIN_REQUIRED":
                    raise HTTPException(
                        409,
                        "Concluindo o salvamento das mensagens já capturadas. O pareamento será liberado quando a fila terminar.",
                    )
            raise HTTPException(
                409 if response.status_code == 409 else 503,
                "Conector indisponível ou ação incompatível com o estado atual",
            )
        result = PublicConnectorStatus.model_validate(response.json())
        persisted = session.scalar(
            select(WhatsAppConnection).where(WhatsAppConnection.slot == "local")
        )
        if persisted:
            result.metrics["captured_total"] = persisted.captured_messages
            result.metrics["last_synced_at"] = (
                persisted.last_synced_at.isoformat() if persisted.last_synced_at else None
            )
        result.metrics["conflicted_messages"] = (
            session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.delivery_state == "conflicted")
            )
            or 0
        )
        return result
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503, "Conector local indisponível. Verifique o serviço e sua configuração."
        ) from None


@public.get("/connection", response_model=PublicConnectorStatus)
async def connection(session: DB) -> PublicConnectorStatus:
    return await connector_request("GET", "/connection", session)


@public.post("/connect", response_model=PublicConnectorStatus)
async def connect(session: DB) -> PublicConnectorStatus:
    return await connector_request("POST", "/connect", session)


@public.post("/disconnect", response_model=PublicConnectorStatus)
async def disconnect(session: DB) -> PublicConnectorStatus:
    return await connector_request("POST", "/disconnect", session)
