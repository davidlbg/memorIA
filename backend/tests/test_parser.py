import pytest

from app.core.ingestion.parser import ParseError, parse_export


@pytest.mark.parametrize(
    ("text", "order", "hour", "day", "month"),
    [
        ("31/10/2026, 18:31 - João: Olá 😀", "DMY", 18, 31, 10),
        ("10/31/26, 6:31 PM - Alice: Hello!", "MDY", 18, 31, 10),
        ("[31/10/2026, 18:31:05] João: Olá", "DMY", 18, 31, 10),
        ("\u200e[2026-10-31, 6:31:05\u202fPM] Alice +55: Hello", "YMD", 18, 31, 10),
        ("31.10.2026, 00:01 - +55 11 99999-0000: teste", "DMY", 0, 31, 10),
        ("10/31/26, 12:01 AM - Alice: midnight", "MDY", 0, 31, 10),
        ("10/31/26, 12:01 PM - Alice: noon", "MDY", 12, 31, 10),
    ],
)
def test_formats(text: str, order: str, hour: int, day: int, month: int) -> None:
    from pydantic import TypeAdapter

    from app.core.ingestion.parser import DateOrder

    preview = parse_export(text.encode(), TypeAdapter(DateOrder).validate_python(order), "UTC")
    message = preview.messages[0]
    assert (message.sent_at.hour, message.sent_at.day, message.sent_at.month) == (hour, day, month)
    assert message.sent_at.tzinfo is not None
    assert message.sender is not None


def test_multiline_media_system_and_preservation() -> None:
    text = "31/10/2026, 10:00 - Messages are end-to-end encrypted\n31/10/2026, 10:01 - João: Um: dois\nlinha com 😀\n31/10/2026, 10:02 - Alice: <Mídia oculta>"
    preview = parse_export(text.encode("utf-16"), "DMY", "America/Sao_Paulo")
    assert preview.encoding == "utf-16"
    assert [m.message_type for m in preview.messages] == ["system", "text", "media_reference"]
    assert preview.messages[1].content == "Um: dois\nlinha com 😀"
    assert preview.messages[0].sender is None
    assert preview.messages[0].sent_at.hour == 13


def test_duplicate_occurrences_preserved_and_hashes_stable() -> None:
    text = b"31/10/2026, 10:00 - Alice: Ok\n31/10/2026, 10:00 - Alice: Ok"
    first = parse_export(text, "DMY", "UTC")
    second = parse_export(text, "DMY", "UTC")
    assert len({m.content_hash for m in first.messages}) == 2
    assert [m.content_hash for m in first.messages] == [m.content_hash for m in second.messages]


@pytest.mark.parametrize(
    "text", [b"", b"random file", b"\x00\x01", b"32/10/2026, 25:01 - Alice: invalid", b"\xffbad"]
)
def test_invalid_files_fail_visibly(text: bytes) -> None:
    with pytest.raises(ParseError):
        parse_export(text, "DMY", "UTC")


def test_ambiguous_order_is_explicit_and_dst_rejected() -> None:
    text = b"01/02/2026, 10:00 - Alice: test"
    assert parse_export(text, "DMY", "UTC").messages[0].sent_at.month == 2
    assert parse_export(text, "MDY", "UTC").messages[0].sent_at.month == 1
    with pytest.raises(ParseError):
        parse_export(b"01/11/2026, 01:30 - Alice: ambiguous", "DMY", "America/New_York")
    with pytest.raises(ParseError):
        parse_export(text, "DMY", "Not/AZone")
