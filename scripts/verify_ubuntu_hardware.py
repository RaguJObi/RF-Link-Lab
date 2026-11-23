"""Verify the Ubuntu hardware patch without implying target or RF execution."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import tarfile
import xml.etree.ElementTree as ET
from build_portable import (VERSION,common_files,ubuntu_hardware_payload,hardware_payload_inventory,
                            verify_ubuntu_hardware_payload,NUAND_SOURCE_COMPANION,NUAND_SOURCE_SHA256)

ROOT=Path(__file__).resolve().parents[1]
archive=ROOT/'release'/f'RF-Link-Lab-v{VERSION}-Ubuntu22-x86_64.tar.gz'
prefix=archive.name.removesuffix('.tar.gz')
count=0
with tarfile.open(archive,'r:gz') as bundle:
    for path in sorted((ROOT/'src/rf_link_lab').glob('*.py')):
        assert bundle.extractfile(f'{prefix}/app/rf_link_lab/{path.name}').read()==path.read_bytes(),path.name
        count+=1
    for path,name in common_files():
        assert bundle.extractfile(f'{prefix}/{name}').read()==path.read_bytes(),name
        count+=1
    for path in (ROOT/'deployment/ubuntu').glob('*.sh'):
        member=bundle.getmember(f'{prefix}/{path.name}')
        assert member.mode&0o111
        assert bundle.extractfile(member).read()==path.read_bytes().replace(b'\r\n',b'\n')
    elf=bundle.extractfile(f'{prefix}/runtime/python/bin/python3.12').read(20)
    assert elf[:4]==b'\x7fELF' and int.from_bytes(elf[18:20],'little')==62
    build=json.load(bundle.extractfile(f'{prefix}/BUILD-MANIFEST.json'))
    assert build['status']=='ASSEMBLED_NOT_EXECUTED_ON_LINUX'
    hardware_inventory=hardware_payload_inventory(ubuntu_hardware_payload())
    assert build['matched_hardware_tools']['files']==hardware_inventory
    verify_ubuntu_hardware_payload(bundle,prefix,hardware_inventory)
companion=ROOT/'release'/NUAND_SOURCE_COMPANION
assert hashlib.sha256(companion.read_bytes()).hexdigest()==NUAND_SOURCE_SHA256
assert companion.with_suffix(companion.suffix+'.sha256').read_text().split()[0]==NUAND_SOURCE_SHA256
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
assert archive.with_suffix(archive.suffix+'.sha256').read_text().split()[0]==digest
suite=ET.parse(ROOT/'build/hardware-tests.xml').getroot().find('testsuite')
assert suite is not None and suite.attrib['errors']=='0' and suite.attrib['failures']=='0'
result={'schema':'rf-link-lab-ubuntu-hardware-release-v1','version':VERSION,
        'checked_utc':datetime.now(timezone.utc).isoformat(),'archive':archive.name,
        'bytes':archive.stat().st_size,'sha256':digest,'current_source_and_resource_files_verified':count,
        'elf_architecture':'x86_64','launcher_modes_and_bytes_verified':True,
        'runtime_and_wheel_hashes_verified_by_builder':True,
        'matched_hardware_payload_files_verified':len(hardware_inventory),
        'matched_tools_and_helpers_hashes_and_modes_verified':True,
        'corresponding_source_companion':{'file':companion.name,'sha256':NUAND_SOURCE_SHA256,'verified':True},
        'software_test_suite':suite.attrib,
        'software_test_scope':'Device-free controller, plan and command integration tests; not physical validation.',
        'target_execution':'pending; Ubuntu and hardware unavailable by user report on 2026-10-21',
        'physical_uart':False,'physical_rf_tx':False,'physical_rf_decode':False,
        'windows_package':'v0.3.0; not rebuilt for this Ubuntu hardware patch'}
output=ROOT/'release'/f'UBUNTU-HARDWARE-VALIDATION-v{VERSION}.json'
output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,indent=2))
