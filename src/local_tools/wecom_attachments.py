"""Consume bounded WeCom attachment bytes before public tool/model projection.

The original Device result/outbox remains the recovery authority. Public results
contain ImageRef only; durable executions reuse their existing image_artifacts.
"""

import asyncio
import base64
import binascii
import hashlib
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


def _decode_image(block):
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


async def materialize_attachment(result, arguments, *, tenant_id, user_id,
                                 tool_call_id, state=None, assembler=None):
    """Replace a requested MCP image block with the existing public ImageRef.

    A missing old-client capability or one unusable image does not poison the
    conversation timeline. Never mutate the original invocation's JSON evidence.
    """
    request = arguments.get('attachment')
    data = result.get('data') if isinstance(result, dict) else None
    attachment = data.get('attachment') if isinstance(data, dict) else None
    if request is None and not isinstance(attachment, dict):
        return result
    if not isinstance(attachment, dict):
        if result.get('success') and request is not None:
            return {**result, 'success': False, 'code': 'TOOL_NOT_SUPPORTED', 'effect': 'none',
                    'message': '当前客户端未返回附件内容，普通聊天读取仍可使用；此附件需要支持按需读取的插件。',
                    'data': {'stage': 'attachment', 'message_id': request['message_id']}}
        return result
    public = {**attachment}
    image = public.pop('image', None)
    projected = {**result, 'data': {**data, 'attachment': public}}
    if request is None or public.get('message_id') != request.get('message_id'):
        public.update(status='unreadable', error_code='ATTACHMENT_REQUEST_MISMATCH',
                      recovery_action='reread_attachment')
        return projected
    if image is None:
        if public.get('kind') == 'image' and public.get('status') == 'available':
            public.update(status='unreadable', error_code='ATTACHMENT_CONTENT_MISSING',
                          recovery_action='reread_attachment')
        return projected
    temporary = None
    try:
        if public.get('kind') != 'image' or public.get('status') != 'available':
            raise ValueError('ATTACHMENT_CONTENT_INVALID')
        content, extension = await asyncio.to_thread(_decode_image, image)
        digest = hashlib.sha256(content).hexdigest()
        prior = next((item for item in (state.resources.get('image_artifacts') or [])
                      if item.get('source') == 'local_attachment' and item.get('sha256') == digest
                      and item.get('image_ref')), None) if state is not None else None
        await asyncio.to_thread(ensure_tenant_storage_dir, tenant_id, 'temp')
        temporary = Path(get_tenant_storage_abs_path(tenant_id, 'temp',
                         'wecom_' + uuid.uuid4().hex[:12] + extension))
        await asyncio.to_thread(temporary.write_bytes, content)
        if prior:
            reference = dict(prior['image_ref'])
        else:
            name = str(public.get('name') or ('attachment' + extension)).replace('\\', '/').rsplit('/', 1)[-1]
            registered = await get_image_registry().register(source_path=temporary,
                tenant_id=tenant_id, user_id=user_id, display_name=name,
                source='user_upload', usage='inline', source_ref='local_tool:' + tool_call_id)
            reference = registered.model_dump()
        if state is not None and assembler is not None and assembler.workspace_root is not None:
            _, artifacts = await asyncio.to_thread(assembler.build_image_reference_content, '', [str(temporary)])
            if not artifacts:
                raise ValueError('ATTACHMENT_RESOURCE_UNAVAILABLE')
            artifact = {**artifacts[0], 'source': 'local_attachment', 'image_ref': reference}
            state.resources['image_artifacts'] = [
                item for item in state.resources.get('image_artifacts', [])
                if item.get('sha256') != digest or item.get('source') != 'local_attachment'] + [artifact]
        public['image'] = reference
        projected['images'] = [reference]
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and str(error).startswith('ATTACHMENT_') else 'ATTACHMENT_RESOURCE_UNAVAILABLE'
        public.update(status='too_large' if code == 'ATTACHMENT_TOO_LARGE' else 'unreadable',
                      error_code=code, recovery_action='reread_attachment')
        logger.warning('Local attachment materialization failed code={}', code)
    finally:
        if temporary is not None:
            try:
                await asyncio.to_thread(temporary.unlink, missing_ok=True)
            except OSError:
                logger.warning('Local attachment temporary cleanup failed code=ATTACHMENT_TEMP_CLEANUP_FAILED')
    return projected
