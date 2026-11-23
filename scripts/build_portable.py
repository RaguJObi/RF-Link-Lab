"""Build a Windows app and assemble a Linux-runtime bundle without cross-build claims."""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile
import zipfile
import shutil

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from rf_link_lab import __version__ as VERSION
OUT=ROOT/'release'
HARDWARE_HELPERS=('START-HARDWARE.sh','PREPARE-MATCHED-BLADERF.sh','RUN-CONNECTED-BENCH.sh')
MATCHED_TOOLS='hardware-tools/nuand-2025.10'
NUAND_COMMIT='fcf9423325ae49f5fdd2e5b52ab68bfc2576ad47'
NUAND_FPGA_SHA256='3004467110e2b2790c8f6128f998c2149905a88eed91a87f11af3db524d60c65'
NUAND_SOURCE_SHA256='d94a6aecf7c57a17541da517f8187a025b634f5c93b5470b2280c8ffe3101b57'
NUAND_SOURCE_COMPANION='Nuand-bladeRF-2025.10-corresponding-source.tar.gz'
MUTABLE_TOOL_LOGS={'linked-libraries.txt','loaded-device.txt'}
NUAND_LICENSES=('COPYING','licenses/LICENSE.AGPLv3','licenses/LICENSE.BSD.msc_inttypes',
                'licenses/LICENSE.BSD.nuand','licenses/LICENSE.BSD.vlfeat','licenses/LICENSE.GPLv2',
                'licenses/LICENSE.LGPLv2.1','licenses/LICENSE.MIT.msc_stdbool','licenses/LICENSE.MIT.nuand')


def checked(path):
    path=path.resolve()
    if not path.is_relative_to(ROOT):raise ValueError('Build target must stay inside the project.')
    return path


def common_files():
    for name in ['README.md','LICENSE','PROGRESS.md','PROJECT_CONTEXT.json','VALIDATION.md','THIRD-PARTY-NOTICES.md']:
        path=ROOT/name
        if path.exists():yield path, name
    for directory in ['docs','artifacts/reference','artifacts/preview']:
        for path in sorted((ROOT/directory).rglob('*')):
            if path.is_file():yield path,path.relative_to(ROOT).as_posix()
    yield ROOT/'deployment/ubuntu/runtime-lock.json','deployment/ubuntu/runtime-lock.json'
    for path in sorted((ROOT/'firmware').rglob('*')):
        if path.is_file() and path.suffix in ('.c','.h','.S','.ld','.md'):yield path,path.relative_to(ROOT).as_posix()
    for path in sorted((ROOT/'artifacts/firmware').glob('*')):
        if path.is_file() and (path.suffix in ('.bin','.hex','.elf','.map') or path.name=='manifest.json'):
            yield path,'firmware/'+path.name


def tar_bytes(target,name,data,mode=0o644):
    info=tarfile.TarInfo(name);info.size=len(data);info.mode=mode;info.mtime=0
    target.addfile(info,io.BytesIO(data))


def _ordinary_hardware_file(folder,relative):
    if (not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in ('','.','..') for part in relative.split('/'))):
        raise ValueError('Unsafe matched-tools manifest path: '+repr(relative))
    path=folder
    for part in relative.split('/'):
        path=path/part
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():
            raise ValueError('Matched-tools cache must contain ordinary files: '+relative)
    if not path.is_file() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError('Missing matched-tools file: '+relative)
    return path


def nuand_corresponding_source():
    source=_ordinary_hardware_file(ROOT/'build/nuand-source-transfer','nuand-2025.10-source.tar.gz')
    data=source.read_bytes()
    if hashlib.sha256(data).hexdigest()!=NUAND_SOURCE_SHA256:
        raise ValueError('Nuand corresponding-source archive hash mismatch.')
    metadata={'file':NUAND_SOURCE_COMPANION,'bytes':len(data),'sha256':NUAND_SOURCE_SHA256,
              'source_tag':'2025.10','source_commit':NUAND_COMMIT,
              'submodules':{'no-OS':'0bba46e6f6f75785a65d425ece37d0a04daf6157',
                            'bladeRF-fsk':'fb903684036217b9db9dcd6be8007548e12a377e'},
              'distribution':'Distribute this release companion with the Ubuntu application archive; it preserves the pinned Nuand source, submodules and per-file notices.',
              'rebuild_note':'Extract source-from-laptop, recreate omitted empty .git/refs directories for the main repository and submodules, and use the pinned PREPARE-MATCHED-BLADERF.sh CMake options. The packaged cache is for runtime use, not a relocatable development SDK.'}
    return source,metadata


def ubuntu_hardware_payload():
    """Validate the actual Linux-built cache before writing a portable archive.

    Windows file permissions do not preserve Linux executable modes, so the
    archive declares modes explicitly. No device or Linux binary is executed.
    """
    cache=ROOT/'build'/MATCHED_TOOLS
    if cache.is_symlink() or getattr(cache,'is_junction',lambda:False)():
        raise ValueError('Matched-tools cache cannot be a link.')
    manifest_path=_ordinary_hardware_file(cache,'MANIFEST.json')
    original=manifest_path.read_bytes()
    manifest=json.loads(original)
    expected={'schema':'rf-link-lab.matched-tools.v1','source_tag':'2025.10',
              'source_commit':NUAND_COMMIT,'cli_version':'1.10.0','library_version':'2.6.0',
              'fpga_version':'0.16.0'}
    if any(manifest.get(key)!=value for key,value in expected.items()):
        raise ValueError('Matched-tools identity does not match the pinned hardware helpers.')
    files=manifest.get('files')
    if not isinstance(files,dict):raise ValueError('Matched-tools cache lacks a file inventory.')
    required={'build-identity.txt','hostedxA9-v0.16.0.rbf','install/bin/bladeRF-cli',
              'install/lib/libbladeRF.so','install/lib/libbladeRF.so.2'}
    required.update('install/share/nuand-licenses/'+name for name in NUAND_LICENSES)
    if not required<=files.keys():raise ValueError('Matched-tools cache lacks required binaries or license notices.')
    actual={path.relative_to(cache).as_posix() for path in cache.rglob('*') if path.is_file()}
    if actual!=set(files)|{'MANIFEST.json'}:
        raise ValueError('Matched-tools cache contains missing or unlisted files.')
    payload={}
    packaged=copy.deepcopy(manifest)
    for name,details in sorted(files.items()):
        path=_ordinary_hardware_file(cache,name)
        data=path.read_bytes()
        sha256=hashlib.sha256(data).hexdigest()
        if (not isinstance(details,dict) or type(details.get('bytes')) is not int
                or details['bytes']!=len(data) or details.get('sha256')!=sha256):
            raise ValueError('Matched-tools hash or size mismatch: '+name)
        mode=0o755 if name.startswith('install/bin/') else 0o644
        packaged['files'][name]['mode']=mode
        if name not in MUTABLE_TOOL_LOGS:payload[MATCHED_TOOLS+'/'+name]=(data,mode)
    for name in MUTABLE_TOOL_LOGS:packaged['files'].pop(name,None)
    if payload[MATCHED_TOOLS+'/build-identity.txt'][0].decode().strip()!=NUAND_COMMIT:
        raise ValueError('Matched-tools build identity differs from the pinned source.')
    if files['hostedxA9-v0.16.0.rbf']['sha256']!=NUAND_FPGA_SHA256:
        raise ValueError('Matched-tools FPGA image is not the pinned xA9 image.')
    for name in ('install/bin/bladeRF-cli','install/lib/libbladeRF.so','install/lib/libbladeRF.so.2'):
        elf=payload[MATCHED_TOOLS+'/'+name][0][:20]
        if len(elf)<20 or elf[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(elf[18:20],'little')!=62:
            raise ValueError('Matched-tools binary is not an ELF64 x86_64 file: '+name)
    _,source_metadata=nuand_corresponding_source()
    if 'source_export_sha256' in packaged:
        packaged['evidence_export_sha256']=packaged.pop('source_export_sha256')
    packaged['corresponding_source_companion']=source_metadata
    packaged['packaging']={'source_manifest_sha256':hashlib.sha256(original).hexdigest(),
                           'archive_modes':'0755 install/bin files; 0644 libraries, data and notices',
                           'excluded_runtime_logs':sorted(MUTABLE_TOOL_LOGS),
                           'evidence_export_note':'Original cache field source_export_sha256 hashes the saved binary/evidence export, not the corresponding-source archive.',
                           'scope':'Linux-built cache identity and bytes; archive assembly does not execute hardware.'}
    payload[MATCHED_TOOLS+'/MANIFEST.json']=(json.dumps(packaged,indent=2).encode()+b'\n',0o644)
    payload['CORRESPONDING-SOURCE.json']=(json.dumps(source_metadata,indent=2).encode()+b'\n',0o644)
    scripts=ROOT/'deployment/hardware'
    if not all((scripts/name).is_file() for name in HARDWARE_HELPERS):
        raise ValueError('The required matched-hardware launchers are missing.')
    for name in HARDWARE_HELPERS:
        if (ROOT/'deployment/ubuntu'/name).exists():
            raise ValueError('Hardware helper duplicates an Ubuntu launcher: '+name)
        path=_ordinary_hardware_file(ROOT/'deployment/hardware',name)
        data=path.read_bytes().replace(b'\r\n',b'\n')
        if not data.startswith(b'#!/usr/bin/env bash\n') or b'\r' in data:
            raise ValueError('Hardware helper must be a Bash script with LF endings: '+name)
        payload[name]=(data,0o755)
    return payload


def hardware_payload_inventory(payload):
    return {name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'mode':mode}
            for name,(data,mode) in sorted(payload.items())}


def write_ubuntu_hardware_payload(target,prefix,payload):
    for name,(data,mode) in sorted(payload.items()):tar_bytes(target,f'{prefix}/{name}',data,mode)


def verify_ubuntu_hardware_payload(bundle,prefix,inventory):
    """Check delivered bytes, regular-file type and exact Linux archive modes."""
    for name,expected in inventory.items():
        item=bundle.getmember(f'{prefix}/{name}')
        if not item.isfile() or item.mode!=expected['mode']:
            raise ValueError('Bundled hardware file type or mode differs: '+name)
        data=bundle.extractfile(item).read()
        if len(data)!=expected['bytes'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:
            raise ValueError('Bundled hardware file hash or size differs: '+name)


def build_ubuntu():
    hardware=ubuntu_hardware_payload()
    hardware_inventory=hardware_payload_inventory(hardware)
    source_companion,source_metadata=nuand_corresponding_source()
    cache=ROOT/'build/linux-inputs'
    manifest=json.loads((cache/'manifest.json').read_text(encoding='utf-8'))
    runtime=cache/manifest['runtime']
    if hashlib.sha256(runtime.read_bytes()).hexdigest()!=manifest['runtime_sha256']:raise ValueError('Linux CPython archive hash mismatch.')
    prefix=f'RF-Link-Lab-v{VERSION}-Ubuntu22-x86_64'
    output=checked(OUT/(prefix+'.tar.gz'));OUT.mkdir(exist_ok=True)
    companion=checked(OUT/NUAND_SOURCE_COMPANION)
    if companion.exists():
        if hashlib.sha256(companion.read_bytes()).hexdigest()!=NUAND_SOURCE_SHA256:
            raise ValueError('Existing corresponding-source companion differs from the pinned input.')
    else:shutil.copyfile(source_companion,companion)
    if hashlib.sha256(companion.read_bytes()).hexdigest()!=NUAND_SOURCE_SHA256:
        raise ValueError('Copied corresponding-source companion failed verification.')
    companion.with_suffix(companion.suffix+'.sha256').write_text(NUAND_SOURCE_SHA256+'  '+companion.name+'\n',encoding='utf-8')
    archive_members=[]
    with tarfile.open(output,'w:gz',compresslevel=5) as target:
        with tarfile.open(runtime,'r:gz') as source:
            for member in source:
                parts=PurePosixPath(member.name)
                if not parts.parts or parts.parts[0]!='python' or '..' in parts.parts or parts.is_absolute():raise ValueError('Unexpected runtime archive path.')
                item=copy.copy(member);item.name=f'{prefix}/runtime/{member.name}'
                item.uid=item.gid=0;item.uname=item.gname='';item.mtime=0
                if item.islnk() and item.linkname.startswith('python/'):item.linkname=f'{prefix}/runtime/{item.linkname}'
                stream=source.extractfile(member) if member.isfile() else None
                target.addfile(item,stream)
                archive_members.append(item.name)
        for wheel in manifest['wheels']:
            path=cache/'wheels'/wheel['name']
            if hashlib.sha256(path.read_bytes()).hexdigest()!=wheel['sha256']:raise ValueError('Linux wheel hash mismatch: '+path.name)
            with zipfile.ZipFile(path) as source:
                for info in source.infolist():
                    if info.is_dir():continue
                    parts=PurePosixPath(info.filename)
                    if parts.is_absolute() or '..' in parts.parts:raise ValueError('Invalid wheel path.')
                    name=info.filename
                    if '.data/' in name:
                        _,tail=name.split('.data/',1);scheme,_,tail=tail.partition('/')
                        if scheme not in ('purelib','platlib'):continue
                        name=tail
                    name=f'{prefix}/runtime/site-packages/{name}'
                    tar_bytes(target,name,source.read(info),0o755 if '.so' in info.filename else 0o644)
                    archive_members.append(name)
        for path in sorted((ROOT/'src/rf_link_lab').glob('*.py')):
            name=f'{prefix}/app/rf_link_lab/{path.name}';tar_bytes(target,name,path.read_bytes());archive_members.append(name)
        for path,name in common_files():
            full=f'{prefix}/{name}';tar_bytes(target,full,path.read_bytes());archive_members.append(full)
        for path in sorted((ROOT/'deployment/ubuntu').glob('*.sh')):
            full=f'{prefix}/{path.name}';tar_bytes(target,full,path.read_bytes().replace(b'\r\n',b'\n'),0o755);archive_members.append(full)
        write_ubuntu_hardware_payload(target,prefix,hardware)
        archive_members.extend(f'{prefix}/{name}' for name in hardware)
        build_manifest={'project_version':VERSION,'assembled_utc':datetime.now(timezone.utc).isoformat(),'target':'Ubuntu22.04 x86_64',
                        'status':'ASSEMBLED_NOT_EXECUTED_ON_LINUX','includes':'Linux CPython executable, Linux binary dependency wheels, application, model, firmware, setup scripts and pinned matched bladeRF tools/FPGA with notices',
                        'hardware_status':'PACKAGE_NOT_HARDWARE_TESTED','runtime_and_wheels':manifest,
                        'matched_hardware_tools':{'source_commit':NUAND_COMMIT,'cache_path':MATCHED_TOOLS,'files':hardware_inventory},
                        'corresponding_source_companion':source_metadata,
                        'members':len(archive_members)}
        tar_bytes(target,f'{prefix}/BUILD-MANIFEST.json',json.dumps(build_manifest,indent=2).encode())
    # Inspect the archive we deliver, including a real ELF header and executable scripts.
    with tarfile.open(output,'r:gz') as check:
        python=check.extractfile(f'{prefix}/runtime/python/bin/python3.12').read(20)
        if python[:4]!=b'\x7fELF' or int.from_bytes(python[18:20],'little')!=62:raise ValueError('Bundled Python is not an x86_64 ELF executable.')
        for name in ['START-RF-LINK-LAB.sh','SETUP-UBUNTU.sh','FLASH-STM32.sh']:
            if not check.getmember(f'{prefix}/{name}').mode&0o111:raise ValueError('Launcher lacks executable mode.')
        if check.extractfile(f'{prefix}/firmware/rf_link_lab_f446re.bin').read()!=(ROOT/'artifacts/firmware/rf_link_lab_f446re.bin').read_bytes():raise ValueError('Firmware copy mismatch.')
        verify_ubuntu_hardware_payload(check,prefix,hardware_inventory)
    return output


def build_windows(package_only=False):
    build=checked(ROOT/'build/windows');dist=checked(ROOT/'dist/windows')
    firmware=checked(ROOT/'build/resources/firmware');firmware.mkdir(parents=True,exist_ok=True)
    for path in (ROOT/'artifacts/firmware').glob('*'):
        if path.is_file() and (path.suffix in ('.bin','.hex','.elf','.map') or path.name=='manifest.json'):shutil.copy2(path,firmware/path.name)
    shutil.copy2(ROOT/'firmware/README.md',firmware/'README.md')
    cmd=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','RF-Link-Lab','--paths',str(ROOT/'src'),
         '--distpath',str(dist),'--workpath',str(build),'--specpath',str(ROOT/'build'),
         '--add-data',str(ROOT/'artifacts/reference/model')+';resources/artifacts/reference/model',
         '--add-data',str(ROOT/'docs')+';resources/docs','--add-data',str(firmware)+';resources/firmware',
         '--exclude-module','PySide6.QtWebEngineCore','--exclude-module','PySide6.QtWebEngineWidgets','--exclude-module','PySide6.QtPdf','--exclude-module','tkinter']
    for package in ['numpy','scipy','scikit-learn','PySide6-Essentials','shiboken6','pyserial']:
        cmd.extend(['--copy-metadata',package])
    for module in ['pandas','pytest','IPython','notebook','torch','tensorflow','jax','sympy','matplotlib','cv2','ziglang']:
        cmd.extend(['--exclude-module',module])
    cmd.append(str(ROOT/'scripts/desktop_entry.py'))
    if not package_only:
        # Resolve Windows system DLLs against Windows itself. A developer PATH
        # can contain an unrelated ICU build (for example Poppler) with the same
        # DLL name but different exports, which makes Qt fail in the frozen app.
        env=os.environ.copy()
        env['PATH']=str(Path(os.environ['SystemRoot'])/'System32')
        for name in ['PYTHONPATH','PYTHONHOME']:env.pop(name,None)
        subprocess.run(cmd,cwd=ROOT,env=env,check=True)
    app=dist/'RF-Link-Lab';output=checked(OUT/f'RF-Link-Lab-v{VERSION}-Windows-x86_64.zip');OUT.mkdir(exist_ok=True)
    if not (app/'RF-Link-Lab.exe').is_file():raise FileNotFoundError('Build the Windows executable before packaging existing output.')
    for path,name in common_files():
        destination=app/'_internal/resources'/name;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,destination)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=5) as target:
        for path in sorted(app.rglob('*')):
            if path.is_file():target.write(path,'RF-Link-Lab/'+path.relative_to(app).as_posix())
        for path,name in common_files():target.write(path,'RF-Link-Lab/'+name)
    return output


def main():
    parser=argparse.ArgumentParser();parser.add_argument('target',choices=['ubuntu','windows']);parser.add_argument('--package-only',action='store_true');args=parser.parse_args()
    output=build_ubuntu() if args.target=='ubuntu' else build_windows(package_only=args.package_only)
    checksum=hashlib.sha256(output.read_bytes()).hexdigest();output.with_suffix(output.suffix+'.sha256').write_text(checksum+'  '+output.name+'\n',encoding='utf-8')
    print(json.dumps({'archive':str(output),'bytes':output.stat().st_size,'sha256':checksum},indent=2))


if __name__=='__main__':main()
