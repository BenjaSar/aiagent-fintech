import ctypes
import os
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from fintech_agent.application.ports import (
    SealedDataCorruptError,
    SealedDataNotFoundError,
)

UI_FORBIDDEN_FLAG = 0x1
VALID_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
FILE_SUFFIX = ".sealed"


class DataBlob(ctypes.Structure):
    _fields_ = (
        ("length", wintypes.DWORD),
        ("data", ctypes.POINTER(ctypes.c_char)),
    )


@dataclass(frozen=True)
class ProtectionRequest:
    content: bytes
    entropy: bytes


def _blob_of(content: bytes) -> tuple[DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(content, len(content))
    blob = DataBlob(len(content), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    return blob, buffer


def _transform(function_name: str, request: ProtectionRequest) -> bytes:
    if sys.platform != "win32":
        raise OSError("Windows DPAPI is only available on Windows")
    content_blob, content_buffer = _blob_of(request.content)
    entropy_blob, entropy_buffer = _blob_of(request.entropy)
    output_blob = DataBlob()
    function = getattr(ctypes.windll.crypt32, function_name)
    succeeded = function(
        ctypes.byref(content_blob),
        None,
        ctypes.byref(entropy_blob),
        None,
        None,
        UI_FORBIDDEN_FLAG,
        ctypes.byref(output_blob),
    )
    del content_buffer, entropy_buffer
    if not succeeded:
        raise OSError(ctypes.get_last_error() or "DPAPI call failed")
    try:
        return ctypes.string_at(output_blob.data, output_blob.length)
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.data)


def protect(request: ProtectionRequest) -> bytes:
    return _transform("CryptProtectData", request)


def unprotect(request: ProtectionRequest) -> bytes:
    return _transform("CryptUnprotectData", request)


class DpapiFileSealedStorage:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def seal(self, name: str, plaintext: bytes) -> None:
        path = self._path_of(name)
        sealed = protect(ProtectionRequest(plaintext, name.encode()))
        self._directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(sealed)
        os.replace(temporary, path)

    def unseal(self, name: str) -> bytes:
        path = self._path_of(name)
        if not path.is_file():
            raise SealedDataNotFoundError(name)
        try:
            return unprotect(ProtectionRequest(path.read_bytes(), name.encode()))
        except OSError as failure:
            raise SealedDataCorruptError(name) from failure

    def _path_of(self, name: str) -> Path:
        if VALID_NAME.fullmatch(name) is None:
            raise ValueError("a sealed name uses lower-case letters, digits and dashes")
        return self._directory / (name + FILE_SUFFIX)
