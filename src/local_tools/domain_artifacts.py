"""Stable recruiting image references prepared before a cursor-only phase.

The domain adapter supplies decoded, already validated image bytes and its
trusted execution/invocation/item identity. No database operation or metadata
cache is performed here; the original fileId URL can recover this shared file.
"""

import hashlib
import json
import os
from pathlib import Path
import uuid

from src.core.storage import configured_storage_root, normalize_tenant_id, _owner_anchor

_EXTENSIONS = {'image/png':'.png', 'image/jpeg':'.jpg', 'image/gif':'.gif'}


def prepare_image_ref(tenant_id, *, execution_id, tool_call_id, invocation_id,
                      item_ordinal, image_ordinal, content, mime_type, name=None):
    if (not all(isinstance(value,str) and value for value in
                (execution_id,tool_call_id,invocation_id))
            or type(item_ordinal) is not int or item_ordinal < 0
            or type(image_ordinal) is not int or image_ordinal < 0):
        raise ValueError('LOCAL_ARTIFACT_OWNER_INVALID')
    if not isinstance(content,bytes) or not content or len(content)>10*1024*1024:
        raise ValueError('LOCAL_ARTIFACT_CONTENT_INVALID')
    extension = _EXTENSIONS.get(mime_type)
    if extension is None:
        raise ValueError('LOCAL_ARTIFACT_TYPE_INVALID')
    digest = hashlib.sha256(content).hexdigest()
    reference = [tenant_id,execution_id,tool_call_id,invocation_id,item_ordinal,
                 image_ordinal,digest,mime_type]
    file_id = 'file_'+uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(reference,separators=(',',':'))).hex
    root = configured_storage_root()
    owner = normalize_tenant_id(tenant_id or '_anonymous')
    directory = _owner_anchor(root,'tenants',owner,'recruiting')
    directory.mkdir(parents=True,exist_ok=True)
    _owner_anchor(root,'tenants',owner,'recruiting')
    target = directory/(file_id+extension)
    if target.resolve()!=target:
        raise ValueError('LOCAL_ARTIFACT_OWNER_INVALID')
    try:
        descriptor = os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
    except FileExistsError:
        # A prior attempt may have prepared the file before its SQL phase. Never
        # overwrite a partial/changed file and pretend that its effect is known.
        if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError('LOCAL_ARTIFACT_VERIFICATION_REQUIRED')
    else:
        try:
            with os.fdopen(descriptor,'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # Only a file created by this call is removable here.
            target.unlink(missing_ok=True)
            raise
    return {'file_id':file_id, 'name':name or target.name}
