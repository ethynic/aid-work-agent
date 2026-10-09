"""Build-machine only: verified CPython/wheels -> a portable, offline OCR directory."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import struct
import tempfile
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent


def download(item, cache):
    target = cache / item['filename']
    if not target.exists():
        with urllib.request.urlopen(item['url'], timeout=90) as response:
            target.write_bytes(response.read())
    if hashlib.sha256(target.read_bytes()).hexdigest() != item['sha256']:
        raise ValueError('Asset digest mismatch: ' + item['filename'])
    return target


def extract(archive, destination, wheel=False):
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                raise ValueError('Unsafe archive path')
            parts = path.parts
            if wheel and parts and parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue  # wheel CLI wrappers are not needed by the embedded runtime
                parts = parts[2:]
            target = destination.joinpath(*parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(bundle.read(entry))


def prepare_msvc(lock, cache, stage):
    """Extract approved app-local CRT files; never execute the redistributable installer."""
    redist = download(lock['redist'], cache)
    command = "$ProgressPreference='SilentlyContinue'; $s=Get-AuthenticodeSignature -LiteralPath '" + str(redist.resolve()).replace("'", "''") + "'; if($s.Status -ne 'Valid' -or $s.SignerCertificate.Subject -notlike '*O=Microsoft Corporation*'){exit 1}"
    signature_env = dict(os.environ)
    signature_env['PSModulePath'] = str(Path(os.environ['SystemRoot']) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'Modules')
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                    base64.b64encode(command.encode('utf-16le')).decode('ascii')], check=True, timeout=60, env=signature_env)
    bootstrap = download(lock['extractor_bootstrap'], cache)
    extra = download(lock['extractor'], cache)
    with tempfile.TemporaryDirectory(prefix='aid-crt-extract-') as temp:
        temp = Path(temp)
        subprocess.run([str(bootstrap.resolve()), 'x', str(extra.resolve()), '-o' + str(temp / 'tools'), '-y'], check=True, stdout=subprocess.DEVNULL)
        extractor = temp / 'tools' / 'x64' / '7za.exe'
        raw = redist.read_bytes()
        cabinets = []
        position = 0
        while True:
            position = raw.find(b'MSCF', position)
            if position < 0:
                break
            size = struct.unpack_from('<I', raw, position + 8)[0]
            if size > 36 and position + size <= len(raw) and raw[position + 24] == 3:
                cabinet = temp / ('container-' + str(len(cabinets)) + '.cab')
                cabinet.write_bytes(raw[position:position + size])
                cabinets.append(cabinet)
            position += 4
        if len(cabinets) != 2:
            raise ValueError('Unexpected verified redistributable format')
        for cabinet, target in zip(cabinets, ['bootstrap', 'packages']):
            subprocess.run([str(extractor), 'x', str(cabinet), '-o' + str(temp / target), '-y'], check=True, stdout=subprocess.DEVNULL)
        manifest = ET.parse(temp / 'bootstrap' / '0').getroot()
        payload = next(x for x in manifest if x.tag.endswith('Payload') and x.attrib.get('FilePath') == 'packages\\vcRuntimeMinimum_amd64\\cab1.cab')
        subprocess.run([str(extractor), 'x', str(temp / 'packages' / payload.attrib['SourcePath']), '-o' + str(temp / 'crt'), '-y'], check=True, stdout=subprocess.DEVNULL)
        for dll in (temp / 'crt').glob('*.dll_amd64'):
            shutil.copyfile(dll, stage / dll.name.removesuffix('_amd64'))
        license_payload = next(x for x in manifest.iter() if x.tag.endswith('Payload') and x.attrib.get('FilePath') == 'license.rtf')
        (stage / 'licenses').mkdir(exist_ok=True)
        shutil.copyfile(temp / 'bootstrap' / license_payload.attrib['SourcePath'], stage / 'licenses' / 'MSVC-14.44-LICENSE.rtf')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'assets' / 'ocr-python-final')
    parser.add_argument('--cache', type=Path, default=ROOT / 'assets' / 'downloads')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Output exists; choose a fresh output directory')
    lock = json.loads((ROOT / 'ocr-assets.lock.json').read_text())
    args.cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='aid-ocr-build-', dir=output.parent) as temp:
        stage = Path(temp)
        extract(download(lock['python'], args.cache), stage)
        site = stage / 'Lib' / 'site-packages'
        site.mkdir(parents=True)
        for wheel in lock['wheels']:
            extract(download(wheel, args.cache), site, wheel=True)
        prepare_msvc(lock['msvc'], args.cache, stage)
        (stage / 'python312._pth').write_text('python312.zip\n.\nLib/site-packages\nimport site\n', encoding='ascii')
        shutil.copyfile(ROOT / 'ocr-assets.lock.json', stage / 'asset-provenance.json')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
        subprocess.run([str(stage / 'python.exe'), '-I', '-B', '-c',
                        'from rapidocr_onnxruntime import RapidOCR; from PIL import Image; import onnxruntime; RapidOCR(); print("OCR_BUNDLE_OK")'],
                       cwd=stage, env=env, check=True, timeout=90)
        shutil.copytree(stage, output)
    print('Verified embedded OCR distribution created:', output)


if __name__ == '__main__':
    main()
