"""Exercise the delivered hardware payload using tiny packaging fixtures.

These tests assemble file archives only; they execute no Linux binary, board,
RF modem, simulation, receiver or system installer.
"""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest


ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('hardware_portable_builder',ROOT/'scripts/build_portable.py')
builder=importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def sha(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(builder,'ROOT',tmp_path)
    monkeypatch.setattr(builder,'OUT',tmp_path/'release')
    monkeypatch.setattr(builder,'VERSION','9.9.9')
    cache=tmp_path/'build'/builder.MATCHED_TOOLS
    cache.mkdir(parents=True)
    elf=bytearray(24)
    elf[:6]=b'\x7fELF\x02\x01'
    elf[18:20]=(62).to_bytes(2,'little')
    fpga=b'FPGA file fixture: never loaded'
    monkeypatch.setattr(builder,'NUAND_FPGA_SHA256',sha(fpga))
    files={'build-identity.txt':(builder.NUAND_COMMIT+'\n').encode(),
           'hostedxA9-v0.16.0.rbf':fpga,'install/bin/bladeRF-cli':bytes(elf),
           'install/lib/libbladeRF.so':bytes(elf),'install/lib/libbladeRF.so.2':bytes(elf),
           'linked-libraries.txt':b'Historical absolute library path\n',
           'loaded-device.txt':b'Historical identity, not a current attached device\n'}
    files.update({'install/share/nuand-licenses/'+name:('License fixture: '+name).encode()
                  for name in builder.NUAND_LICENSES})
    manifest={'schema':'rf-link-lab.matched-tools.v1','source_tag':'2025.10',
              'source_commit':builder.NUAND_COMMIT,'cli_version':'1.10.0','library_version':'2.6.0',
              'fpga_version':'0.16.0','source_export_sha256':'original-evidence-hash','files':{}}
    for name,data in files.items():
        path=cache/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
        manifest['files'][name]={'bytes':len(data),'sha256':sha(data)}
    (cache/'MANIFEST.json').write_text(json.dumps(manifest))
    scripts=tmp_path/'deployment/hardware'
    scripts.mkdir(parents=True)
    for name in (*builder.HARDWARE_HELPERS,'INSTALL-RF-LINK-LAB.sh','START-PHYSICAL-BENCH.sh','RUN-ONE-SESSION.sh'):
        (scripts/name).write_bytes(b'#!/usr/bin/env bash\r\n# Packaging fixture, no commands.\r\n')
    source=tmp_path/'build/nuand-source-transfer/nuand-2025.10-source.tar.gz'
    source.parent.mkdir(parents=True)
    with tarfile.open(source,'w:gz') as archive:
        builder.tar_bytes(archive,'source-from-laptop/COPYING',b'Source fixture')
    monkeypatch.setattr(builder,'NUAND_SOURCE_SHA256',sha(source.read_bytes()))
    return dict(root=tmp_path,cache=cache,manifest=manifest,scripts=scripts,source=source)


def test_hardware_payload_retains_licenses_and_modes_without_mutable_logs(inputs):
    payload=builder.ubuntu_hardware_payload()
    manifest=json.loads(payload[builder.MATCHED_TOOLS+'/MANIFEST.json'][0])
    assert set(manifest['files'])==set(inputs['manifest']['files'])-builder.MUTABLE_TOOL_LOGS
    assert manifest['packaging']['excluded_runtime_logs']==sorted(builder.MUTABLE_TOOL_LOGS)
    assert manifest['evidence_export_sha256']=='original-evidence-hash'
    assert 'source_export_sha256' not in manifest
    assert manifest['corresponding_source_companion']['sha256']==builder.NUAND_SOURCE_SHA256
    for name in builder.HARDWARE_HELPERS:
        data,mode=payload[name]
        assert mode==0o755 and b'\r' not in data
    assert all(name not in payload for name in ('INSTALL-RF-LINK-LAB.sh','START-PHYSICAL-BENCH.sh','RUN-ONE-SESSION.sh'))
    assert payload[builder.MATCHED_TOOLS+'/install/bin/bladeRF-cli'][1]==0o755
    assert payload[builder.MATCHED_TOOLS+'/install/lib/libbladeRF.so.2'][1]==0o644
    for name in builder.NUAND_LICENSES:
        assert builder.MATCHED_TOOLS+'/install/share/nuand-licenses/'+name in payload
    assert all(builder.MATCHED_TOOLS+'/'+name not in payload for name in builder.MUTABLE_TOOL_LOGS)


@pytest.mark.parametrize('damage',['hash','missing_license','unlisted','wrong_identity','not_elf','unsafe_name','source_hash'])
def test_hardware_payload_rejects_unverifiable_distribution_inputs(inputs,damage):
    cache=inputs['cache'];manifest=inputs['manifest']
    if damage=='hash':(cache/'install/bin/bladeRF-cli').write_bytes(b'Changed binary')
    elif damage=='missing_license':
        name='install/share/nuand-licenses/licenses/LICENSE.GPLv2'
        del manifest['files'][name]
    elif damage=='unlisted':(cache/'unlisted.txt').write_text('Not inventoried')
    elif damage=='wrong_identity':manifest['source_commit']='0'*40
    elif damage=='not_elf':
        data=b'Non-ELF with an updated hash';(cache/'install/lib/libbladeRF.so').write_bytes(data)
        manifest['files']['install/lib/libbladeRF.so']={'bytes':len(data),'sha256':sha(data)}
    elif damage=='unsafe_name':manifest['files']['../outside.txt']={'bytes':1,'sha256':'0'*64}
    elif damage=='source_hash':inputs['source'].write_bytes(b'Changed source archive')
    (cache/'MANIFEST.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):builder.ubuntu_hardware_payload()
    assert not (inputs['root']/'release').exists()


@pytest.mark.parametrize('damage',['mode','bytes','link'])
def test_archive_verification_catches_wrong_delivered_modes_bytes_and_links(inputs,damage):
    payload=builder.ubuntu_hardware_payload();inventory=builder.hardware_payload_inventory(payload)
    name='START-HARDWARE.sh';data,mode=payload[name];stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        for relative,(content,archive_mode) in payload.items():
            if relative==name and damage=='link':
                item=tarfile.TarInfo('fixture/'+name);item.type=tarfile.SYMTYPE;item.linkname='elsewhere';archive.addfile(item)
            else:
                builder.tar_bytes(archive,'fixture/'+relative,
                                  b'Changed script' if relative==name and damage=='bytes' else content,
                                  0o644 if relative==name and damage=='mode' else archive_mode)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        with pytest.raises(ValueError):builder.verify_ubuntu_hardware_payload(archive,'fixture',inventory)


def test_tiny_complete_ubuntu_build_delivers_hardware_and_source_companion(inputs,monkeypatch):
    root=inputs['root'];linux=root/'build/linux-inputs';linux.mkdir(parents=True)
    runtime=linux/'runtime.tar.gz'
    elf=bytearray(24);elf[:4]=b'\x7fELF';elf[18:20]=(62).to_bytes(2,'little')
    with tarfile.open(runtime,'w:gz') as archive:
        builder.tar_bytes(archive,'python/bin/python3.12',bytes(elf),0o755)
    (linux/'manifest.json').write_text(json.dumps({'runtime':runtime.name,'runtime_sha256':sha(runtime.read_bytes()),'wheels':[]}))
    (root/'src/rf_link_lab').mkdir(parents=True)
    (root/'src/rf_link_lab/__init__.py').write_text('__version__="9.9.9"')
    ubuntu=root/'deployment/ubuntu';ubuntu.mkdir(parents=True)
    for name in ('START-RF-LINK-LAB.sh','SETUP-UBUNTU.sh','FLASH-STM32.sh'):
        (ubuntu/name).write_bytes(b'#!/usr/bin/env bash\n# No commands.\n')
    firmware=root/'artifacts/firmware/rf_link_lab_f446re.bin';firmware.parent.mkdir(parents=True);firmware.write_bytes(b'Not a device image')
    monkeypatch.setattr(builder,'common_files',lambda:iter([(firmware,'firmware/rf_link_lab_f446re.bin')]))
    output=builder.build_ubuntu();prefix=output.name.removesuffix('.tar.gz')
    with tarfile.open(output) as archive:
        manifest=json.load(archive.extractfile(prefix+'/BUILD-MANIFEST.json'))
        builder.verify_ubuntu_hardware_payload(archive,prefix,manifest['matched_hardware_tools']['files'])
        assert archive.getmember(prefix+'/RUN-CONNECTED-BENCH.sh').mode==0o755
        assert all(prefix+'/'+name not in archive.getnames() for name in ('INSTALL-RF-LINK-LAB.sh','START-PHYSICAL-BENCH.sh'))
        assert manifest['hardware_status']=='PACKAGE_NOT_HARDWARE_TESTED'
    companion=output.parent/builder.NUAND_SOURCE_COMPANION
    assert companion.read_bytes()==inputs['source'].read_bytes()
    assert companion.with_suffix(companion.suffix+'.sha256').read_text().split()[0]==sha(companion.read_bytes())
