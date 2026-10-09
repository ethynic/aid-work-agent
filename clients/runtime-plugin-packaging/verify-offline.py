"""Isolated build-machine smoke. No business GUI inputs, npm, pip or Git in payload execution."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    return {str(path.relative_to(root)).replace('\\', '/'): {'size': path.stat().st_size, 'sha256': digest(path)} for path in root.rglob('*') if path.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    args = parser.parse_args()
    trust = args.directory / 'ISOLATED-TEST-TRUST-ROOT.json'
    summaries = []
    for archive in sorted(args.directory.glob('*.aidplugin.zip')):
        with tempfile.TemporaryDirectory(prefix='aid-offline-smoke-') as temp:
            temp = Path(temp)
            with zipfile.ZipFile(archive) as outer:
                if sorted(outer.namelist()) != ['envelope.json', 'payload.zip']:
                    raise ValueError('Outer container shape mismatch')
                envelope = outer.read('envelope.json')
                payload = outer.read('payload.zip')
            (temp / 'envelope.json').write_bytes(envelope)
            (temp / 'payload.zip').write_bytes(payload)
            with zipfile.ZipFile(io.BytesIO(payload)) as bundle:
                (temp / 'manifest.json').write_bytes(bundle.read('runtime-manifest.json'))
                subprocess.run([str(args.node.resolve()), str(ROOT / 'verify-signature.mjs'), str(temp / 'envelope.json'), str(temp / 'payload.zip'), str(temp / 'manifest.json'), str(trust.resolve())], check=True, capture_output=True)
                stage = temp / 'relocated'
                stage.mkdir()
                for entry in bundle.infolist():
                    path = PurePosixPath(entry.filename)
                    if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or entry.is_dir():
                        raise ValueError('Unexpected payload path')
                    destination = stage.joinpath(*path.parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(bundle.read(entry))
            manifest = json.loads((stage / 'runtime-manifest.json').read_text(encoding='utf-8'))
            before = snapshot(stage)
            expected = {file['path']: {'size': file['size'], 'sha256': file['sha256']} for file in manifest['distribution']['required_files']}
            if {key: value for key, value in before.items() if key != 'runtime-manifest.json'} != expected:
                raise ValueError('Required-files inventory mismatch')
            windows = Path(os.environ['SystemRoot'])
            appdata = temp / 'appdata'
            appdata.mkdir()
            env = {key: os.environ[key] for key in ['SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'USERNAME'] if key in os.environ}
            env.update({'PATH': str(windows / 'System32') + ';' + str(windows / 'System32/WindowsPowerShell/v1.0'), 'LOCALAPPDATA': str(appdata), 'APPDATA': str(appdata)})
            cli = str(stage / 'dist/src/cli/index.js')
            reported = json.loads(subprocess.check_output([str(args.node.resolve()), cli, 'version', '--json'], cwd=stage, env=env, timeout=30))
            for field in ['provider_id', 'provider_version', 'entrypoint', 'tools', 'schema_digest', 'protocol', 'transport']:
                if reported[field] != manifest[field]:
                    raise ValueError('Version contract mismatch: ' + field)
            doctor = subprocess.run([str(args.node.resolve()), cli, 'doctor', '--json'], cwd=stage, env=env, timeout=30, capture_output=True)
            report = json.loads(doctor.stdout)
            if doctor.returncode not in [0, 1] or not isinstance(report['runtime_readiness']['ready'], bool):
                raise ValueError('Doctor did not return truthful structured readiness')
            if (stage / 'ocr-python').exists():
                code = "from PIL import Image,ImageDraw,ImageFont; from rapidocr_onnxruntime import RapidOCR; import sys,json; im=Image.new('RGB',(700,160),'white'); ImageDraw.Draw(im).text((25,40),'Runtime 123',fill='black',font=ImageFont.truetype(sys.argv[1],48)); result,_=RapidOCR()(im); assert result and any('Runtime' in x[1] for x in result); print('SYNTHETIC_OCR_OK')"
                subprocess.run([str(stage / 'ocr-python/python.exe'), '-I', '-B', '-c', code, str(windows / 'Fonts/arial.ttf')], cwd=stage, env=env, check=True, timeout=90, capture_output=True)
            if snapshot(stage) != before:
                raise ValueError('Read-only smoke mutated signed payload')
            summaries.append({'archive': archive.name, 'sha256': digest(archive), 'size': archive.stat().st_size, 'files': len(before), 'tools': len(manifest['tools']), 'doctor_exit': doctor.returncode, 'runtime_readiness': report['runtime_readiness'], 'payload_unchanged': True, 'synthetic_ocr': (stage / 'ocr-python').exists()})
    print(json.dumps(summaries, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
