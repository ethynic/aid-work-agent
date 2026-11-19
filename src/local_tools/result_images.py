"""Bounded image staging and private registration for local Device results."""

import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
import uuid

from loguru import logger
from PIL import Image

from src.core.image_asset import get_image_registry
from src.core.storage import ensure_tenant_storage_dir, get_tenant_storage_abs_path

MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_FORMATS = {'image/png': ('PNG', '.png'), 'image/jpeg': ('JPEG', '.jpg'),
                 'image/gif': ('GIF', '.gif'), 'image/webp': ('WEBP', '.webp')}


def decode_image_block(block):
    if not isinstance(block, dict) or block.get('type') != 'image':
        raise ValueError('ATTACHMENT_CONTENT_INVALID')
    kind = IMAGE_FORMATS.get(block.get('mimeType'))
    encoded = block.get('data')
    if kind is None or not isinstance(encoded, str):
        raise ValueError('ATTACHMENT_CONTENT_INVALID')
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError('ATTACHMENT_TOO_LARGE')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('ATTACHMENT_CONTENT_INVALID') from error
    if not content or len(content) > MAX_IMAGE_BYTES:
        raise ValueError('ATTACHMENT_TOO_LARGE' if content else 'ATTACHMENT_CONTENT_INVALID')
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != kind[0]:
                raise ValueError('ATTACHMENT_CONTENT_INVALID')
            image.verify()
    except (OSError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError('ATTACHMENT_CONTENT_INVALID') from error
    return content, kind[1]



@asynccontextmanager
async def stage_result_image(block, tenant_id):
    content, extension = await asyncio.to_thread(decode_image_block, block)
    await asyncio.to_thread(ensure_tenant_storage_dir, tenant_id, 'temp')
    temporary = Path(get_tenant_storage_abs_path(
        tenant_id, 'temp', 'local_result_' + uuid.uuid4().hex[:12] + extension))
    try:
        await asyncio.to_thread(temporary.write_bytes, content)
        yield temporary, content
    finally:
        try:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
        except OSError:
            logger.warning('Local attachment temporary cleanup failed code=ATTACHMENT_TEMP_CLEANUP_FAILED')


async def register_result_image(temporary, *, tenant_id, user_id, name, source, source_ref):
    registered = await get_image_registry().register(
        source_path=temporary, tenant_id=tenant_id, user_id=user_id,
        display_name=Path(name.replace('\\', '/')).name, source=source,
        usage='inline', source_ref=source_ref)
    return registered.model_dump()
