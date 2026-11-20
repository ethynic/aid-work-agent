"""Build signed declarations with ephemeral test keys. No external files are needed."""

import base64
import hashlib
import json

import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


PROVIDER = 'sample-mcp'
TOOL = 'sample_mcp_action'


def definition(field, description='原生 MCP 工具描述', *, name=TOOL):
    return {'name': name, 'description': description, 'inputSchema': {
        'type': 'object', 'properties': {field: {'type': 'string'}},
        'required': [field], 'additionalProperties': False,
    }}


def signed_registry(root):
    key = Ed25519PrivateKey.generate()
    provider_id = f'ai.aidwork.{PROVIDER}'

    def write(path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')

    write(root / 'trust-roots.json', {'schemaVersion': 1, 'profile': 'production', 'roots': [{
        'key_id': 'publisher', 'providers': [provider_id],
        'public_key': key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode('ascii'),
    }]})

    def register(device, *tools, declared=None):
        directory = root / str(device['id']) / PROVIDER
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {'manifest_version': 1, 'provider_id': provider_id, 'provider_version': '1.0.0',
                    'protocol': 'mcp', 'transport': 'stdio', 'execution_target': 'local_required',
                    'tools': list(tools), 'tool_declaration': {'version': 1, 'tools': [
                        {'name': tool['name']} for tool in tools if declared is None or tool['name'] in declared]}}
        envelope = {'envelope_version': 1, 'package_format': 'zip', 'signature_algorithm': 'Ed25519',
                    'publisher_key_id': 'publisher', 'provider_id': provider_id, 'provider_version': '1.0.0',
                    'manifest_digest': f'sha256:{hashlib.sha256(rfc8785.dumps(manifest)).hexdigest()}'}
        envelope['signature'] = 'base64:' + base64.b64encode(key.sign(rfc8785.dumps(envelope))).decode('ascii')
        write(directory / 'envelope.json', envelope)
        write(directory / 'runtime-manifest.json', manifest)
        return directory / 'runtime-manifest.json'

    return register
