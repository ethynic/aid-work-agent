"""Load the signed tool definitions registered for one selected device."""

import base64
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from loguru import logger


def _unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('插件清单包含重复 JSON 字段')
    return result


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_unique_fields)
    if not isinstance(value, dict):
        raise ValueError('插件清单 JSON 必须为对象')
    return value


def _verify_manifest(directory: Path, provider_key: str, trust: dict) -> dict:
    envelope = _read_json(directory / 'envelope.json')
    manifest = _read_json(directory / 'runtime-manifest.json')
    provider_id = f'ai.aidwork.{provider_key}'
    if (envelope.get('envelope_version') != 1 or envelope.get('package_format') != 'zip'
            or envelope.get('signature_algorithm') != 'Ed25519' or manifest.get('manifest_version') != 1
            or envelope.get('provider_id') != provider_id or manifest.get('provider_id') != provider_id
            or not manifest.get('provider_version') or envelope.get('provider_version') != manifest['provider_version']
            or manifest.get('protocol') != 'mcp' or manifest.get('transport') != 'stdio'
            or manifest.get('execution_target') != 'local_required'):
        raise ValueError('插件身份或清单格式不匹配')
    roots = [root for root in trust['roots'] if isinstance(root, dict)
             and root.get('key_id') == envelope.get('publisher_key_id')]
    if len(roots) != 1:
        raise ValueError('插件发布者不受信任')
    root = roots[0]
    pem = root.get('public_key')
    if (not isinstance(root.get('providers'), list) or provider_id not in root['providers']
            or (trust['profile'] == 'production' and root.get('test_only'))
            or not isinstance(pem, str) or not pem.startswith('-----BEGIN PUBLIC KEY-----')):
        raise ValueError('插件发布者无权签署此插件')
    key = load_pem_public_key(pem.encode('ascii'))
    signature = envelope.pop('signature', None)
    if not isinstance(key, Ed25519PublicKey) or not isinstance(signature, str) or not signature.startswith('base64:'):
        raise ValueError('插件签名格式无效')
    key.verify(base64.b64decode(signature[7:], validate=True), rfc8785.dumps(envelope))
    if envelope.get('manifest_digest') != f'sha256:{hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()}':
        raise ValueError('插件清单摘要不匹配')
    return manifest


def _tool_definitions(manifest: dict, device_names: list[str]) -> list[dict]:
    declaration = manifest.get('tool_declaration')
    definitions = manifest.get('tools')
    if not isinstance(declaration, dict) or declaration.get('version') != 1 or not isinstance(definitions, list):
        raise ValueError('缺少工具声明')
    tools = {}
    for tool in definitions:
        if isinstance(tool, dict) and isinstance(tool.get('name'), str) and tool['name']:
            name = tool['name']
            if name in tools:
                raise ValueError('插件清单内工具重名')
            tools[name] = tool
    entries = declaration.get('tools')
    if not isinstance(entries, list) or not entries:
        raise ValueError('插件工具声明无效')
    names = set()
    visible = []
    device_names = set(device_names)
    for entry in entries:
        name = entry.get('name') if isinstance(entry, dict) else None
        if (not isinstance(name, str) or name in names or name not in tools
                or ('parallel' in entry and type(entry['parallel']) is not bool)):
            raise ValueError('插件工具声明与清单不匹配')
        names.add(name)
        tool = tools[name]
        if (name in device_names and isinstance(tool.get('description', ''), str)
                and isinstance(tool.get('inputSchema'), dict)):
            visible.append(tool)
    return visible


def load_device_tools(root: str, device_id: str, declared: dict[str, list[str]]) -> list[tuple[str, dict]]:
    if not root or not declared:
        return []
    root = Path(root)
    device_path = root / str(UUID(device_id))
    if not device_path.is_dir():
        return []
    try:
        trust = _read_json(root / 'trust-roots.json')
        if (trust.get('schemaVersion') != 1 or trust.get('profile') not in ('acceptance', 'production')
                or not isinstance(trust.get('roots'), list)):
            raise ValueError('插件发布者信任格式无效')
    except (OSError, ValueError) as error:
        logger.warning('无法加载 Runtime 插件发布者信任，device={}, error={}', device_id, type(error).__name__)
        return []
    tools = []
    for provider_key, names in declared.items():
        if not names or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', provider_key):
            continue
        directory = device_path / provider_key
        if not directory.is_dir():
            continue
        try:
            manifest = _verify_manifest(directory, provider_key, trust)
            tools.extend((provider_key, item) for item in _tool_definitions(manifest, names))
        except (OSError, ValueError, RecursionError, InvalidSignature) as error:
            logger.warning('Runtime 插件登记无效，device={}, provider={}, error={}',
                           device_id, provider_key, type(error).__name__)
    return tools
