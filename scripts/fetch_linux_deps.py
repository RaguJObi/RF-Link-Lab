"""Fetch official portable CPython and Linux binary wheels for an Ubuntu bundle.

This assembles Linux files on Windows; it does not claim Linux execution.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'build/linux-inputs'


def fetch(url,path):
    if path.exists():return
    request=urllib.request.Request(url,headers={'User-Agent':'RF-Link-Lab-build/0.2'})
    with urllib.request.urlopen(request,timeout=90) as response,path.open('wb') as output:
        while chunk:=response.read(1024*1024):output.write(chunk)


def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    lock=ROOT/'deployment/ubuntu/runtime-lock.json'
    if lock.is_file():
        manifest=json.loads(lock.read_text(encoding='utf-8'))
        runtime=CACHE/manifest['runtime']
        fetch(manifest['runtime_url'],runtime)
        if hashlib.sha256(runtime.read_bytes()).hexdigest()!=manifest['runtime_sha256']:
            raise ValueError('Locked CPython archive hash mismatch.')
        wheels=CACHE/'wheels';wheels.mkdir(exist_ok=True)
        for wheel in manifest['wheels']:
            filename=wheel['name'];path=wheels/filename
            if not path.exists():
                package,version=filename.split('-',2)[:2]
                request=urllib.request.Request(f'https://pypi.org/pypi/{package}/{version}/json',headers={'User-Agent':'RF-Link-Lab-build/0.2'})
                with urllib.request.urlopen(request,timeout=60) as response:metadata=json.load(response)
                asset=next(a for a in metadata['urls'] if a['filename']==filename and a['digests']['sha256']==wheel['sha256'])
                fetch(asset['url'],path)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=wheel['sha256']:
                raise ValueError('Locked Linux wheel hash mismatch: '+filename)
        (CACHE/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
        print('Locked Linux runtime and wheels present; all hashes verified.',flush=True)
        return
    url='https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest'
    request=urllib.request.Request(url,headers={'User-Agent':'RF-Link-Lab-build/0.2'})
    with urllib.request.urlopen(request,timeout=60) as response:release=json.load(response)
    assets=[a for a in release['assets'] if a['name'].startswith('cpython-3.12.') and a['name'].endswith('x86_64-unknown-linux-gnu-install_only.tar.gz')]
    if not assets:raise RuntimeError('No baseline x86_64 GNU Linux CPython3.12 install-only asset in official release.')
    asset=sorted(assets,key=lambda a:tuple(int(p) for p in a['name'].split('+')[0].removeprefix('cpython-').split('.')))[-1]
    runtime=CACHE/asset['name'];print(f"Downloading official runtime {runtime.name}",flush=True)
    fetch(asset['browser_download_url'],runtime)
    digest=hashlib.sha256(runtime.read_bytes()).hexdigest()
    published=asset.get('digest')
    if published and published!='sha256:'+digest:raise ValueError('Official CPython asset digest mismatch.')
    wheels=CACHE/'wheels';wheels.mkdir(exist_ok=True)
    cmd=[sys.executable,'-m','pip','download','--only-binary=:all:','--dest',str(wheels),'--python-version','312','--implementation','cp','--abi','cp312','--abi','abi3']
    for platform in ['manylinux_2_35_x86_64','manylinux_2_34_x86_64','manylinux_2_28_x86_64','manylinux_2_27_x86_64','manylinux2014_x86_64','manylinux_2_17_x86_64','linux_x86_64']:
        cmd.extend(['--platform',platform])
    cmd.extend(['numpy>=1.24,<3','scipy>=1.10,<2','scikit-learn>=1.5,<2','PySide6-Essentials>=6.7,<7','pyserial>=3.5,<4'])
    subprocess.run(cmd,check=True)
    manifest={'runtime':runtime.name,'runtime_url':asset['browser_download_url'],'runtime_sha256':digest,'official_digest':published,'runtime_release':release['tag_name'],'target':'Ubuntu22.04 x86_64/glibc2.35','execution_status':'not executed on Linux in this Windows build environment','wheels':[]}
    for p in sorted(wheels.glob('*.whl')):manifest['wheels'].append({'name':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    (CACHE/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print('Linux runtime and wheels downloaded and hashed.',flush=True)


if __name__=='__main__':main()
