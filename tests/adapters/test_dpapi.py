import sys
from pathlib import Path

import pytest

from fintech_agent.adapters.dpapi import (
    DpapiFileSealedStorage,
    ProtectionRequest,
    protect,
    unprotect,
)
from fintech_agent.application.ports import (
    SealedDataCorruptError,
    SealedDataNotFoundError,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Windows DPAPI is only available on Windows"
)

SECRET = b"\x07" * 32


def test_protected_bytes_hide_the_secret_and_round_trip() -> None:
    protected = protect(ProtectionRequest(SECRET, b"entropy"))

    assert SECRET not in protected
    assert unprotect(ProtectionRequest(protected, b"entropy")) == SECRET


def test_the_wrong_entropy_cannot_unprotect() -> None:
    protected = protect(ProtectionRequest(SECRET, b"entropy"))

    with pytest.raises(OSError):
        unprotect(ProtectionRequest(protected, b"other"))


def test_sealed_files_round_trip_and_never_contain_the_plaintext(
    tmp_path: Path,
) -> None:
    storage = DpapiFileSealedStorage(tmp_path / "node-1")

    storage.seal("key-share", SECRET)

    assert storage.unseal("key-share") == SECRET
    assert SECRET not in (tmp_path / "node-1" / "key-share.sealed").read_bytes()


def test_a_missing_entry_is_reported(tmp_path: Path) -> None:
    with pytest.raises(SealedDataNotFoundError):
        DpapiFileSealedStorage(tmp_path).unseal("key-share")


def test_a_corrupted_file_is_reported(tmp_path: Path) -> None:
    storage = DpapiFileSealedStorage(tmp_path)
    storage.seal("key-share", SECRET)
    path = tmp_path / "key-share.sealed"
    damaged = bytearray(path.read_bytes())
    damaged[-1] ^= 0xFF
    path.write_bytes(bytes(damaged))

    with pytest.raises(SealedDataCorruptError):
        storage.unseal("key-share")


def test_a_file_copied_to_another_name_cannot_be_unsealed(tmp_path: Path) -> None:
    storage = DpapiFileSealedStorage(tmp_path)
    storage.seal("key-share", SECRET)
    (tmp_path / "other.sealed").write_bytes(
        (tmp_path / "key-share.sealed").read_bytes()
    )

    with pytest.raises(SealedDataCorruptError):
        storage.unseal("other")


@pytest.mark.parametrize("name", ["../escape", "Upper", "a/b", "", "-leading"])
def test_names_that_could_leave_the_directory_are_rejected(
    tmp_path: Path, name: str
) -> None:
    with pytest.raises(ValueError):
        DpapiFileSealedStorage(tmp_path).seal(name, SECRET)
