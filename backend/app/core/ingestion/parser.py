import hashlib
import re
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field

DateOrder = Literal["DMY", "MDY", "YMD"]
BOUNDARY = re.compile(
    r"^[\u200e\u200f\ufeff]*\[?(?P<date>\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp]\.?[Mm]\.?)?)"
    r"(?:\]\s*|\s+[-–]\s+)(?P<body>.*)$"
)
MEDIA = re.compile(
    r"<(?:media omitted|mídia oculta|media oculto|imagem ocultada|áudio ocultado|vídeo omitido)>|\(file attached\)|\(arquivo anexado\)|<attached:",
    re.I,
)


class ParseError(ValueError):
    pass


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class ParsedMessage(BaseModel):
    sender: str | None
    content: str
    sent_at: datetime
    message_type: Literal["text", "system", "media_reference"]
    content_hash: str = ""
    line: int


class Preview(BaseModel):
    messages: list[ParsedMessage]
    total_messages: int
    participants: list[str]
    formats: list[str]
    warnings: list[str] = Field(default_factory=list)
    file_hash: str
    encoding: str


def decode_export(data: bytes) -> tuple[str, str]:
    if b"\x00" in data and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ParseError("Arquivo binário não é uma exportação TXT.")
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    try:
        return data.decode(encoding), encoding
    except UnicodeDecodeError as exc:
        raise ParseError(
            "Use TXT UTF-8 ou UTF-16 com BOM; a codificação não foi reconhecida."
        ) from exc


def parse_timestamp(date: str, time: str, order: DateOrder, zone: ZoneInfo) -> datetime:
    values = [int(part) for part in re.split(r"[/.-]", date)]
    if len(re.split(r"[/.-]", date)[0]) == 4:  # ISO-like year first
        year, month, day = values
    elif order == "YMD":
        year, month, day = values
    elif order == "DMY":
        day, month, year = values
    else:
        month, day, year = values
    if year < 100:
        year += 2000
    cleaned = time.replace(".", "").upper().replace("\u202f", " ")
    cleaned = re.sub(r"\s*(AM|PM)$", r" \1", cleaned)
    fmt = "%I:%M" if "AM" in cleaned or "PM" in cleaned else "%H:%M"
    if cleaned.count(":") == 2:
        fmt += ":%S"
    if "AM" in cleaned or "PM" in cleaned:
        fmt += " %p"
    clock = datetime.strptime(cleaned, fmt).time()
    local = datetime(year, month, day, clock.hour, clock.minute, clock.second)
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ParseError("Horário ambíguo ou inexistente por mudança de fuso; revise a exportação.")
    return first.astimezone(UTC)


def parse_export(data: bytes, order: DateOrder, timezone: str) -> Preview:
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ParseError("Fuso horário IANA inválido.") from exc
    text, encoding = decode_export(data)
    messages: list[ParsedMessage] = []
    formats: set[str] = set()
    warnings: list[str] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        boundary = BOUNDARY.match(line)
        if boundary:
            try:
                stamp = parse_timestamp(boundary["date"], boundary["time"], order, zone)
            except ValueError as exc:
                raise ParseError(
                    f"Data/hora inválida na linha {number}; confira a ordem da data e o fuso."
                ) from exc
            body = boundary["body"]
            sender: str | None = None
            content = body
            # WhatsApp's TXT format has no escaping: ': ' is its sender separator.
            if ": " in body:
                possible, content = body.split(": ", 1)
                sender = possible.strip("\u200e\u200f")
                if not sender or len(sender) > 500:
                    raise ParseError(f"Remetente inválido na linha {number}.")
            kind: Literal["text", "system", "media_reference"] = "text" if sender else "system"
            if sender and MEDIA.search(content):
                kind = "media_reference"
            messages.append(
                ParsedMessage(
                    sender=sender, content=content, sent_at=stamp, message_type=kind, line=number
                )
            )
            formats.add(
                "iOS/brackets"
                if line.lstrip("\u200e\u200f\ufeff").startswith("[")
                else "Android/dash"
            )
        elif messages:
            messages[-1].content += "\n" + line
        elif line.strip():
            raise ParseError(
                f"Texto anterior à primeira mensagem na linha {number}; revise o arquivo."
            )
    if not messages:
        raise ParseError("Nenhuma mensagem WhatsApp reconhecida.")
    if len(messages) > 100_000 or any(len(message.content) > 100_000 for message in messages):
        raise ParseError("Excedido o limite de mensagens ou tamanho de uma mensagem.")
    occurrences: Counter[str] = Counter()
    for message in messages:
        # Preserve identical messages that really occurred repeatedly in one minute.
        canonical = "\x1f".join(
            [
                message.sent_at.isoformat(),
                message.sender or "",
                message.message_type,
                message.content,
            ]
        )
        ordinal = occurrences[canonical]
        occurrences[canonical] += 1
        message.content_hash = hashlib.sha256(f"{canonical}\x1f{ordinal}".encode()).hexdigest()
    warnings.append(
        "TXT não distingue uma linha de continuação que imita um cabeçalho. Confira a prévia."
    )
    warnings.append(
        "Datas de dois dígitos são interpretadas como 2000–2099. Nomes não comprovam identidade."
    )
    return Preview(
        messages=messages,
        total_messages=len(messages),
        participants=sorted({m.sender for m in messages if m.sender}),
        formats=sorted(formats),
        warnings=warnings,
        file_hash=hashlib.sha256(data).hexdigest(),
        encoding=encoding,
    )
