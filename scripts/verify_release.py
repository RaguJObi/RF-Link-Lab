"""Verify final archive contents against current source and built firmware."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import tarfile
import xml.etree.ElementTree as ET
import zipfile

from build_portable import common_files,VERSION

ROOT=Path(__file__).resolve().parents[1]
release=ROOT/'release'
linux=release/f'RF-Link-Lab-v{VERSION}-Ubuntu22-x86_64.tar.gz'
windows=release/f'RF-Link-Lab-v{VERSION}-Windows-x86_64.zip'
checked=[]
prefix=f'RF-Link-Lab-v{VERSION}-Ubuntu22-x86_64'
with tarfile.open(linux,'r:gz') as archive:
    for path in sorted((ROOT/'src/rf_link_lab').glob('*.py')):
        assert archive.extractfile(f'{prefix}/app/rf_link_lab/{path.name}').read()==path.read_bytes(),f'Stale Linux code: {path.name}'
    for path in (ROOT/'deployment/ubuntu').glob('*.sh'):
        member=archive.getmember(f'{prefix}/{path.name}')
        assert member.mode&0o111
        assert archive.extractfile(member).read()==path.read_bytes().replace(b'\r\n',b'\n')
    for path,name in common_files():
        assert archive.extractfile(f'{prefix}/{name}').read()==path.read_bytes(),f'Stale Linux resource: {name}'
    for path in (ROOT/'artifacts/firmware').glob('*'):
        if path.is_file() and path.suffix in ('.bin','.hex','.elf','.map'):
            assert archive.extractfile(f'{prefix}/firmware/{path.name}').read()==path.read_bytes()
    interpreter=archive.extractfile(f'{prefix}/runtime/python/bin/python3.12').read(20)
    assert interpreter[:4]==b'\x7fELF' and int.from_bytes(interpreter[18:20],'little')==62
    status=json.load(archive.extractfile(f'{prefix}/BUILD-MANIFEST.json'))
    assert status['status']=='ASSEMBLED_NOT_EXECUTED_ON_LINUX'
    assert archive.getmember(f'{prefix}/artifacts/reference/healthy/capture.npz')
with zipfile.ZipFile(windows) as archive:
    assert archive.read('RF-Link-Lab/RF-Link-Lab.exe')==(ROOT/'dist/windows/RF-Link-Lab/RF-Link-Lab.exe').read_bytes()
    for path,name in common_files():
        assert archive.read('RF-Link-Lab/'+name)==path.read_bytes(),f'Stale Windows resource: {name}'
        assert archive.read('RF-Link-Lab/_internal/resources/'+name)==path.read_bytes(),f'Stale embedded Windows resource: {name}'
    for name in ['model.json','evaluation.json']:
        assert archive.read('RF-Link-Lab/_internal/resources/artifacts/reference/model/'+name)==(ROOT/'artifacts/reference/model'/name).read_bytes()
for path in [linux,windows]:
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    assert path.with_suffix(path.suffix+'.sha256').read_text().split()[0]==digest
    checked.append({'file':path.name,'bytes':path.stat().st_size,'sha256':digest})
smoke=json.loads((release/'WINDOWS-SELF-TEST.json').read_text(encoding='utf-8'))
assert smoke['passed'] and smoke['mode']=='fresh_zip_extraction'
assert smoke['archive_sha256']==checked[1]['sha256'],'Windows execution evidence is for an older archive.'
suite=ET.parse(ROOT/'build/test-results.xml').getroot().find('testsuite')
assert suite is not None and suite.attrib['failures']=='0' and suite.attrib['errors']=='0'
report={'schema':'rf-link-lab-release-validation-v1','version':VERSION,
        'validated_utc':datetime.now(timezone.utc).isoformat(),
        'archive_integrity_and_current_sources':True,'archives':checked,
        'python_test_suite':suite.attrib,
        'windows':smoke,
        'ubuntu':{'status':'ASSEMBLED_NOT_EXECUTED_ON_LINUX','runtime_and_wheel_hashes_verified':True,
                  'elf_architecture_verified':'x86_64','launcher_modes_and_contents_verified':True},
        'firmware':{'status':'BUILT_NOT_HARDWARE_TESTED','manifest':json.loads((ROOT/'artifacts/firmware/manifest.json').read_text(encoding='utf-8'))},
        'hardware':{'connected':False,'flashed':False,'usb_streaming_tested':False,'rf_transmitted':False}}
(release/'VALIDATION-SUMMARY.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({'archive_integrity_and_current_sources':True,'archives':checked,'windows_fresh_extraction_passed':True,'ubuntu_executed':False},indent=2))
