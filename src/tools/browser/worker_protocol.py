"""浏览器 worker 的长度前缀 IPC 协议。

帧格式固定为 ``4-byte unsigned big-endian length + UTF-8 JSON``，单帧上限
1 MiB。协议错误从不回显原始 payload。
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any, BinaryIO

MAX_FRAME_BYTES = 1024 * 1024
HEADER_BYTES = 4


class ProtocolError(Exception):
    """可安全公开类型、不可公开原始帧的协议错误。"""


class FrameTooLarge(ProtocolError):
    pass


class TruncatedFrame(ProtocolError):
    pass


class InvalidFrame(ProtocolError):
    pass


def encode_frame(message: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidFrame("invalid_json") from exc
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameTooLarge("frame_too_large")
    return struct.pack(">I", len(payload)) + payload


def decode_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) > MAX_FRAME_BYTES:
        raise FrameTooLarge("frame_too_large")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidFrame("invalid_json") from exc
    if not isinstance(value, dict):
        raise InvalidFrame("message_must_be_object")
    return value


def read_frame_sync(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(HEADER_BYTES)
    if header == b"":
        return None
    if len(header) != HEADER_BYTES:
        raise TruncatedFrame("truncated_header")
    length = struct.unpack(">I", header)[0]
    if length > MAX_FRAME_BYTES:
        raise FrameTooLarge("frame_too_large")
    payload = stream.read(length)
    if len(payload) != length:
        raise TruncatedFrame("truncated_payload")
    return decode_payload(payload)


def write_frame_sync(stream: BinaryIO, message: dict[str, Any]) -> None:
    stream.write(encode_frame(message))
    stream.flush()


async def read_frame(reader: asyncio.StreamReader) -> dict[str, Any]:
    try:
        header = await reader.readexactly(HEADER_BYTES)
    except asyncio.IncompleteReadError as exc:
        raise TruncatedFrame("truncated_header") from exc
    length = struct.unpack(">I", header)[0]
    if length > MAX_FRAME_BYTES:
        raise FrameTooLarge("frame_too_large")
    try:
        payload = await reader.readexactly(length)
    except asyncio.IncompleteReadError as exc:
        raise TruncatedFrame("truncated_payload") from exc
    return decode_payload(payload)


async def write_frame(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    writer.write(encode_frame(message))
    await writer.drain()
