"""Execute the built application without the development environment on PATH."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--archive',type=Path,help='Validate a fresh extraction of the delivered ZIP.')
args=parser.parse_args()
binary=ROOT/'dist/windows/RF-Link-Lab/RF-Link-Lab.exe'
work=ROOT/'build';work.mkdir(exist_ok=True)
state=Path(tempfile.mkdtemp(prefix='windows-smoke-',dir=work))
if args.archive:
    destination=state/'extracted'
    with zipfile.ZipFile(args.archive) as archive:
        for item in archive.infolist():
            target=(destination/item.filename).resolve()
            if not target.is_relative_to(destination.resolve()):raise ValueError('Unsafe ZIP entry.')
        archive.extractall(destination)
    binary=destination/'RF-Link-Lab/RF-Link-Lab.exe'
output=state/'self-test.json'
env=os.environ.copy()
for name in ['PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','RF_LINK_LAB_RESOURCES']:env.pop(name,None)
env.update({'QT_QPA_PLATFORM':'offscreen','RF_LINK_LAB_HOME':str(state),
            'PATH':str(Path(os.environ['SystemRoot'])/'System32')})
result=subprocess.run([str(binary),'self-test','--with-ui','--output',str(output)],cwd=state,env=env,timeout=120,capture_output=True)
if result.returncode:raise RuntimeError(f'Frozen application self-test exited {result.returncode}; inspect {state / "startup-error.log"}')
report=json.loads(output.read_text(encoding='utf-8'))
if not report['passed'] or not report['environment']['frozen_executable']:raise RuntimeError('Frozen application self-test did not pass.')
summary={'passed':True,'mode':'fresh_zip_extraction' if args.archive else 'build_directory',
         'executable':str(binary),'executable_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
         'report':str(output),'checks':report['checks'],'scope':report['scope'],
         'archive':str(args.archive.resolve()) if args.archive else None,
         'archive_sha256':hashlib.sha256(args.archive.read_bytes()).hexdigest() if args.archive else None}
(ROOT/'release').mkdir(exist_ok=True)
(ROOT/'release/WINDOWS-SELF-TEST.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary,indent=2))
