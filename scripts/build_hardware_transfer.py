"""Create a versioned source/history/runtime kit for Ubuntu installation or update."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from rf_link_lab import __version__ as VERSION
from build_portable import NUAND_SOURCE_COMPANION


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stamp',required=True,help='Client-local date as YYYYMMDD')
    args=parser.parse_args()
    if len(args.stamp)!=8 or not args.stamp.isdigit():parser.error('stamp must be YYYYMMDD')
    if subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip():
        raise RuntimeError('Commit the intended project source before creating its transfer snapshot.')
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    kit=ROOT/'release'/f'transfer-v{VERSION}-{args.stamp}'
    kit.mkdir(parents=True,exist_ok=False)
    source=kit/'RF-Link-Lab-source.tar.gz'
    subprocess.run(['git','archive','--format=tar.gz','--prefix=RF-Link-Lab/','--output',str(source),'HEAD'],cwd=ROOT,check=True)
    subprocess.run(['git','bundle','create',str(kit/'RF-Link-Lab-history.bundle'),'--all'],cwd=ROOT,check=True)
    subprocess.run(['git','bundle','verify',str(kit/'RF-Link-Lab-history.bundle')],cwd=ROOT,check=True,capture_output=True)
    archive=ROOT/'release'/f'RF-Link-Lab-v{VERSION}-Ubuntu22-x86_64.tar.gz'
    expected=archive.with_suffix(archive.suffix+'.sha256').read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected:raise ValueError('Ubuntu archive hash mismatch.')
    for path in [archive,archive.with_suffix(archive.suffix+'.sha256'),
                 ROOT/'release'/NUAND_SOURCE_COMPANION,
                 (ROOT/'release'/NUAND_SOURCE_COMPANION).with_suffix('.gz.sha256'),
                 ROOT/'release'/f'UBUNTU-HARDWARE-VALIDATION-v{VERSION}.json',
                 ROOT/'deployment/hardware/INSTALL-RF-LINK-LAB.sh',
                 ROOT/'deployment/hardware/UPDATE-RF-LINK-LAB.sh',
                 ROOT/'deployment/hardware/UPDATE-UBUNTU.cmd',
                 ROOT/'deployment/hardware/TRANSFER-TO-UBUNTU.ps1',
                 ROOT/'docs/HARDWARE-FIRST-RUN.md',
                 ROOT/'docs/HARDWARE-AI.md',
                 ROOT/'docs/READY-FOR-HARDWARE.md']:
        shutil.copyfile(path,kit/path.name)
    (kit/'SOURCE-COMMIT.txt').write_text(commit+'\n',encoding='ascii')
    files=[]
    for path in sorted(kit.iterdir()):
        files.append({'file':path.name,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (kit/'SHA256SUMS').write_text(''.join(row['sha256']+'  '+row['file']+'\n' for row in files),encoding='ascii',newline='\n')
    report={'kit':str(kit),'source_commit':commit,'bytes':sum(row['bytes'] for row in files),'files':files}
    (kit/'KIT-MANIFEST.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
