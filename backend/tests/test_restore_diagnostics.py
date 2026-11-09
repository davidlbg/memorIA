import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "restore_diagnostics_fixture",
    Path(__file__).resolve().parents[2] / "scripts" / "verify_backup.py",
)
assert spec is not None and spec.loader is not None
restore = importlib.util.module_from_spec(spec)
spec.loader.exec_module(restore)


@pytest.mark.parametrize(
    ("diagnostic", "expected"),
    [
        (
            b'pg_restore: error: could not execute query: ERROR:  could not resize shared memory segment "private-name"\nCONTEXT: private-message-body',
            "shared_memory_limit",
        ),
        (
            b"pg_restore: error: error returned by PQsendQuery: server closed the connection unexpectedly\nprivate-message-body",
            "connection_interrupted",
        ),
        (
            b"pg_restore: error: could not execute query: ERROR:  private-message-body\nCONTEXT: private-name",
            "UNKNOWN_restore_error",
        ),
    ],
)
def test_restore_diagnostics_expose_only_fixed_categories(diagnostic: bytes, expected: str) -> None:
    result = restore.restore_error_category(diagnostic)
    assert result == expected
    assert "private-" not in result
