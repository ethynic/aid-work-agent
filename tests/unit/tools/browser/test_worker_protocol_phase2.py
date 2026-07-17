import asyncio
import io
import struct

import pytest

from src.tools.browser.worker_protocol import (
    MAX_FRAME_BYTES, FrameTooLarge, InvalidFrame, TruncatedFrame, decode_payload,
    encode_frame, read_frame,
)


def test_frame_uses_four_byte_big_endian_and_json_utf8():
    frame = encode_frame({"type": "测试", "seq": 1})
    assert struct.unpack(">I", frame[:4])[0] == len(frame) - 4
    assert decode_payload(frame[4:]) == {"type": "测试", "seq": 1}


@pytest.mark.asyncio
async def test_fragmented_frame_is_reassembled():
    frame = encode_frame({"type": "snapshot", "seq": 2})
    reader = asyncio.StreamReader()
    task = asyncio.create_task(read_frame(reader))
    for chunk in (frame[:1], frame[1:4], frame[4:9], frame[9:]):
        reader.feed_data(chunk)
        await asyncio.sleep(0)
    assert await task == {"type": "snapshot", "seq": 2}


def test_oversize_invalid_and_truncated_frames_are_rejected():
    with pytest.raises(FrameTooLarge):
        encode_frame({"payload": "x" * (MAX_FRAME_BYTES + 1)})
    with pytest.raises(InvalidFrame):
        decode_payload(b"[]")
    from src.tools.browser.worker_protocol import read_frame_sync
    with pytest.raises(TruncatedFrame):
        read_frame_sync(io.BytesIO(b"\x00\x00"))
    with pytest.raises(TruncatedFrame):
        read_frame_sync(io.BytesIO(struct.pack(">I", 5) + b"ab"))


@pytest.mark.asyncio
async def test_unknown_message_is_rejected_by_real_worker_subprocess():
    process = await asyncio.create_subprocess_exec(
        __import__("sys").executable, "-m", "src.tools.browser.worker_main",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    from src.tools.browser.worker_protocol import write_frame
    await write_frame(process.stdin, {"type": "unknown", "run_id": "br_bad", "seq": 1, "command_id": "bc_unknown"})
    response = await asyncio.wait_for(read_frame(process.stdout), timeout=5)
    assert response["error_code"] == "UNKNOWN_MESSAGE"
    process.stdin.close()
    await asyncio.wait_for(process.wait(), timeout=5)
