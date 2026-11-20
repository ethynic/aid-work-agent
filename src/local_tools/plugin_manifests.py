"""Verify signed heartbeat manifests and expose the current device's tool definitions."""

import base64
import hashlib
import json
import re
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from loguru import logger


VERIFIED_MANIFESTS = '_verified_plugin_manifests'
MANIFEST_DIGESTS = 'plugin_manifest_digests'
PLUGIN_PHASE = 'runtime_plugin'
PLUGIN_CHANGED_CODE = 'LOCAL_DEVICE_PLUGIN_CHANGED'
PLUGIN_CHANGED_MESSAGE = '本机插件清单已变化，本次操作未执行。'


def client_capabilities(capabilities):
    if capabilities is None or VERIFIED_MANIFESTS not in capabilities:
        return capabilities
    return {key: value for key, value in capabilities.items() if key != VERIFIED_MANIFESTS}


def _unique_fields(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('插件清单包含重复 JSON 字段')
    return result


def _signed_material(bundle):
    import rfc8785

    envelope = bundle.get('envelope') if isinstance(bundle, dict) else None
    manifest = bundle.get('manifest') if isinstance(bundle, dict) else None
    if not isinstance(envelope, dict) or not isinstance(manifest, dict):
        raise ValueError('插件清单必须为对象')
    unsigned = {name: value for name, value in envelope.items() if name != 'signature'}
    return {'envelope': rfc8785.dumps(unsigned).decode('utf-8'),
            'manifest': rfc8785.dumps(manifest).decode('utf-8'), 'signature': envelope.get('signature')}


def _verify_manifest(material, digest, provider_key, trust):
    # The old API could store arbitrary capabilities keys. Prove the original
    # signature on every use, even for cached data written before this revision.
    if (not isinstance(material, dict) or not isinstance(material.get('envelope'), str)
            or not isinstance(material.get('manifest'), str)):
        raise ValueError('插件清单缓存无效')
    envelope = json.loads(material['envelope'], object_pairs_hook=_unique_fields)
    provider_id = f'ai.aidwork.{provider_key}'
    if (not isinstance(envelope, dict) or envelope.get('envelope_version') != 1
            or envelope.get('package_format') != 'zip' or envelope.get('signature_algorithm') != 'Ed25519'
            or envelope.get('manifest_digest') != digest
            or envelope.get('provider_id') != provider_id):
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
    signature = material.get('signature')
    if not isinstance(key, Ed25519PublicKey) or not isinstance(signature, str) or not signature.startswith('base64:'):
        raise ValueError('插件签名格式无效')
    key.verify(base64.b64decode(signature[7:], validate=True), material['envelope'].encode('utf-8'))
    if digest != f"sha256:{hashlib.sha256(material['manifest'].encode('utf-8')).hexdigest()}":
        raise ValueError('插件清单摘要不匹配')
    manifest = json.loads(material['manifest'], object_pairs_hook=_unique_fields)
    if (not isinstance(manifest, dict) or manifest.get('manifest_version') != 1
            or manifest.get('provider_id') != provider_id or not manifest.get('provider_version')
            or envelope.get('provider_version') != manifest['provider_version']
            or manifest.get('protocol') != 'mcp' or manifest.get('transport') != 'stdio'
            or manifest.get('execution_target') != 'local_required'):
        raise ValueError('插件身份或清单格式不匹配')
    return manifest


def _tool_definitions(manifest: dict) -> list[dict]:
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
    for entry in entries:
        name = entry.get('name') if isinstance(entry, dict) else None
        if (not isinstance(name, str) or name in names or name not in tools
                or ('parallel' in entry and type(entry['parallel']) is not bool)):
            raise ValueError('插件工具声明与清单不匹配')
        names.add(name)
        tool = tools[name]
        if (isinstance(tool.get('description', ''), str)
                and isinstance(tool.get('inputSchema'), dict)):
            visible.append(tool)
    return visible


def _trust_roots(path):
    try:
        trust = json.loads(Path(path).read_bytes(), object_pairs_hook=_unique_fields)
        if (not isinstance(trust, dict) or trust.get('schemaVersion') != 1
                or trust.get('profile') not in ('acceptance', 'production')
                or not isinstance(trust.get('roots'), list)):
            raise ValueError('插件发布者信任格式无效')
    except (OSError, ValueError, RecursionError) as error:
        logger.warning('无法加载 Runtime 插件发布者信任，error={}', type(error).__name__)
        return None
    return trust


def _stored_bundles(capabilities):
    bundles = (capabilities or {}).get(VERIFIED_MANIFESTS)
    return bundles if isinstance(bundles, dict) else {}


def sync_plugin_manifests(path, device_id, capabilities, previous, bundles):
    """Only the authenticated heartbeat writes projections. Call under the device row lock."""
    capabilities = dict(client_capabilities(capabilities))
    digests = capabilities.get(MANIFEST_DIGESTS)
    if not isinstance(digests, dict) or not digests or not (trust := _trust_roots(path)):
        return capabilities, []
    cached = _stored_bundles(previous)
    saved = {}
    missing = []
    for provider, digest in digests.items():
        if not (isinstance(provider, str) and re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]*', provider)
                and isinstance(digest, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', digest)):
            continue
        if provider in cached:
            try:
                _verify_manifest(cached[provider], digest, provider, trust)
                saved[provider] = cached[provider]
                continue
            except (ValueError, RecursionError, InvalidSignature):
                pass
        bundle = (bundles or {}).get(provider)
        if bundle is not None:
            try:
                material = _signed_material(bundle)
                _tool_definitions(_verify_manifest(material, digest, provider, trust))
                saved[provider] = material
                continue
            except (ValueError, RecursionError, InvalidSignature, ImportError) as error:
                logger.warning('Runtime 插件清单验签失败，device={}, provider={}, error={}',
                               device_id, provider, type(error).__name__)
        missing.append(provider)
    if saved:
        capabilities[VERIFIED_MANIFESTS] = saved
    return capabilities, missing


def load_device_tools(path, capabilities, declared):
    if not path or not declared:
        return []
    cached = _stored_bundles(capabilities)
    digests = (capabilities or {}).get(MANIFEST_DIGESTS)
    if not cached or not isinstance(digests, dict) or not (trust := _trust_roots(path)):
        return []
    tools = []
    for provider, names in declared.items():
        digest = digests.get(provider)
        if not names or not isinstance(digest, str) or provider not in cached:
            continue
        try:
            signed = _verify_manifest(cached[provider], digest, provider, trust)
            tools.extend((provider, tool) for tool in _tool_definitions(signed) if tool['name'] in names)
        except (ValueError, RecursionError, InvalidSignature):
            continue
    return tools
