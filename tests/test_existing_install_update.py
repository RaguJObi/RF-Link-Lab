"""Local tiny Git/bundle/runtime updates; no application or hardware is run."""

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'deployment/hardware/UPDATE-RF-LINK-LAB.sh'
GIT = shutil.which('git')
BASH = (str(Path(r'C:\Program Files\Git\bin\bash.exe')) if os.name == 'nt'
        and Path(r'C:\Program Files\Git\bin\bash.exe').is_file() else shutil.which('bash'))
pytestmark = [pytest.mark.skipif(not BASH or not GIT, reason='Git and Bash are required'),
              pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0,
                                 reason='Updater intentionally refuses root')]
ARCHIVE = 'RF-Link-Lab-v0.3.2-Ubuntu22-x86_64.tar.gz'
COMPANION = 'Nuand-bladeRF-2025.10-corresponding-source.tar.gz'
RUNTIME = ARCHIVE[:-7]


def bash_path(path):
    value = Path(path).as_posix()
    return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value


def git(folder, *args):
    return subprocess.check_output([GIT, '-C', str(folder), *args], text=True, stderr=subprocess.PIPE).strip()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='\n')


def add_tar(archive, name, data=b'', mode=0o644, kind=None, link=''):
    entry = tarfile.TarInfo(name)
    entry.mode = mode
    if kind is not None:
        entry.type, entry.linkname = kind, link
    else:
        entry.size = len(data)
    archive.addfile(entry, io.BytesIO(data) if kind is None else None)


def runtime_archive(kit, *, unsafe=False, wrong_version=False):
    metadata = {'project': 'RF Link Lab', 'version': '0.3.9' if wrong_version else '0.3.2'}
    with tarfile.open(kit / ARCHIVE, 'w:gz') as archive:
        add_tar(archive, RUNTIME + '/PROJECT_CONTEXT.json', json.dumps(metadata).encode())
        for name in ('START-RF-LINK-LAB.sh', 'START-HARDWARE.sh', 'PREPARE-MATCHED-BLADERF.sh', 'RUN-CONNECTED-BENCH.sh'):
            add_tar(archive, RUNTIME + '/' + name, b'#!/usr/bin/env bash\nexit 97\n', 0o755)
        elf = b'\x7fELF\x02\x01' + b'\0' * 12 + (62).to_bytes(2, 'little')
        add_tar(archive, RUNTIME + '/runtime/python/bin/python3.12', elf, 0o755)
        add_tar(archive, RUNTIME + '/runtime/site-packages', kind=tarfile.DIRTYPE, mode=0o755)
        add_tar(archive, RUNTIME + '/app/rf_link_lab/__init__.py', b'__version__ = "0.3.2"\n')
        add_tar(archive, RUNTIME + '/BUILD-MANIFEST.json', b'{"project_version":"0.3.2"}')
        corresponding = {'file': COMPANION, 'sha256': hashlib.sha256((kit / COMPANION).read_bytes()).hexdigest()}
        add_tar(archive, RUNTIME + '/CORRESPONDING-SOURCE.json', json.dumps(corresponding).encode())
        if unsafe is True:
            add_tar(archive, '../escaped.txt', b'must not escape staging')
        elif unsafe == 'chain':
            add_tar(archive, RUNTIME + '/b', kind=tarfile.SYMTYPE, link='.')
            add_tar(archive, RUNTIME + '/a', kind=tarfile.SYMTYPE, link='b/../source')
        elif unsafe == 'cycle':
            add_tar(archive, RUNTIME + '/a', kind=tarfile.SYMTYPE, link='b')
            add_tar(archive, RUNTIME + '/b', kind=tarfile.SYMTYPE, link='a')


def refresh_checksums(kit):
    for name in (ARCHIVE, COMPANION):
        value = hashlib.sha256((kit / name).read_bytes()).hexdigest()
        write(kit / (name + '.sha256'), value + '  ' + name + '\n')
    records = []
    for path in sorted(kit.iterdir()):
        if path.name != 'SHA256SUMS':
            records.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    write(kit / 'SHA256SUMS', ''.join(records))


@pytest.fixture
def installation(tmp_path):
    source, project, kit = (tmp_path / name for name in ('source', 'existing project', 'transfer kit'))
    source.mkdir()
    git(source, 'init', '-b', 'main')
    git(source, 'config', 'user.name', 'Updater test')
    git(source, 'config', 'user.email', 'updater@example.invalid')
    write(source / '.gitignore', 'build/\nrelease/\nruntime-ubuntu/\nartifacts/bench/\n')
    write(source / 'PROJECT_CONTEXT.json', json.dumps({'project': 'RF Link Lab', 'version': '0.3.1'}))
    write(source / 'README.md', 'Installed source\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'Installed baseline')
    before = git(source, 'rev-parse', 'HEAD')
    subprocess.run([GIT, 'clone', str(source), str(project)], check=True, capture_output=True)
    old_runtime = project / 'runtime-ubuntu/RF-Link-Lab-v0.3.1-Ubuntu22-x86_64/keep.txt'
    old_artifact = project / 'artifacts/bench/existing-session/keep.txt'
    old_release = project / 'release/older-release.txt'
    for path in (old_runtime, old_artifact, old_release):
        write(path, 'preserve this existing evidence\n')
    write(source / 'PROJECT_CONTEXT.json', json.dumps({'project': 'RF Link Lab', 'version': '0.3.2'}))
    write(source / 'README.md', 'Updated source\n')
    git(source, 'add', '.')
    git(source, 'commit', '-m', 'New release')
    pinned = git(source, 'rev-parse', 'HEAD')
    kit.mkdir()
    git(source, 'bundle', 'create', str(kit / 'RF-Link-Lab-history.bundle'), '--all')
    write(kit / 'SOURCE-COMMIT.txt', pinned + '\n')
    (kit / COMPANION).write_bytes(b'corresponding source fixture; not executed')
    runtime_archive(kit)
    refresh_checksums(kit)
    stubs = tmp_path / 'stub tools'
    write(stubs / 'python3', '#!/usr/bin/env bash\nexec "$TEST_UPDATE_PYTHON" "$@"\n')
    write(stubs / 'flock', '#!/usr/bin/env bash\n[[ $1 == -n && $2 == 9 ]]\n: >&9\n')
    for path in stubs.iterdir():
        path.chmod(0o755)
    return {'project': project, 'kit': kit, 'source': source, 'before': before, 'pinned': pinned,
            'stubs': stubs, 'preserved': (old_runtime, old_artifact, old_release)}


def run_update(installation):
    env = dict(os.environ, TEST_UPDATE_PYTHON=Path(sys.executable).as_posix(),
               TEST_UPDATE_STUBS=bash_path(installation['stubs']))
    return subprocess.run(
        [BASH, '-c', 'export PATH="$TEST_UPDATE_STUBS:$PATH"; exec bash "$@"', 'test-local-update',
         bash_path(SCRIPT), '--project', bash_path(installation['project']), '--kit', bash_path(installation['kit'])],
        env=env, text=True, capture_output=True, timeout=40,
    )


def assert_preserved(installation):
    assert all(path.read_text() == 'preserve this existing evidence\n' for path in installation['preserved'])


def assert_refused(installation, text):
    result = run_update(installation)
    assert result.returncode != 0, result.stdout
    assert text in result.stderr, result.stderr
    assert git(installation['project'], 'rev-parse', 'HEAD') == installation['before']
    assert_preserved(installation)
    return result


def test_existing_install_fast_forwards_and_preserves_evidence_without_running_apps(installation):
    marker = installation['project'] / 'hook-was-run.txt'
    write(installation['project'] / '.git/hooks/post-merge',
          '#!/usr/bin/env bash\nprintf bad > "' + marker.as_posix() + '"\n')
    (installation['project'] / '.git/hooks/post-merge').chmod(0o755)
    result = run_update(installation)
    assert result.returncode == 0, result.stderr
    project = installation['project']
    assert git(project, 'rev-parse', 'HEAD') == installation['pinned']
    assert (project / 'README.md').read_text() == 'Updated source\n'
    assert not marker.exists()
    assert_preserved(installation)
    for name in (ARCHIVE, ARCHIVE + '.sha256', COMPANION, COMPANION + '.sha256'):
        assert (project / 'release' / name).read_bytes() == (installation['kit'] / name).read_bytes()
    helper = project / 'runtime-ubuntu' / RUNTIME / 'RUN-CONNECTED-BENCH.sh'
    assert helper.is_file() and str(helper) in result.stdout
    assert not git(project, 'status', '--porcelain')
    records = list((project / '.git/rf-link-lab-updates').glob('*/update.json'))
    assert len(records) == 1 and json.loads(records[0].read_text())['phase'] == 'complete'
    second = run_update(installation)
    assert second.returncode != 0 and 'same-version runtime preserved' in second.stderr
    assert helper.is_file() and git(project, 'rev-parse', 'HEAD') == installation['pinned']


def test_dirty_source_is_rejected_without_source_update(installation):
    write(installation['project'] / 'README.md', 'Uncommitted user change\n')
    assert_refused(installation, 'not clean')
    assert (installation['project'] / 'README.md').read_text() == 'Uncommitted user change\n'


def test_corrupt_kit_is_rejected_before_staging_or_source_update(installation):
    with (installation['kit'] / ARCHIVE).open('ab') as stream:
        stream.write(b'corruption')
    assert_refused(installation, 'checksum mismatch')
    assert not (installation['project'] / '.git/rf-link-lab-updates').exists()


def test_bundle_main_must_match_pinned_commit(installation):
    write(installation['kit'] / 'SOURCE-COMMIT.txt', installation['before'] + '\n')
    refresh_checksums(installation['kit'])
    assert_refused(installation, 'bundle main commit differs')


def test_diverged_clean_source_is_not_reset(installation):
    project = installation['project']
    git(project, 'config', 'user.name', 'Local user')
    git(project, 'config', 'user.email', 'local@example.invalid')
    write(project / 'local-source.txt', 'keep local commit\n')
    git(project, 'add', 'local-source.txt')
    git(project, 'commit', '-m', 'Local source work')
    installation['before'] = git(project, 'rev-parse', 'HEAD')
    assert_refused(installation, 'not a fast-forward')
    assert (project / 'local-source.txt').read_text() == 'keep local commit\n'


def test_same_version_runtime_is_never_overwritten(installation):
    path = installation['project'] / 'runtime-ubuntu' / RUNTIME / 'user-file.txt'
    write(path, 'existing same-version runtime\n')
    assert_refused(installation, 'same-version runtime preserved')
    assert path.read_text() == 'existing same-version runtime\n'


def test_conflicting_release_file_is_preserved(installation):
    path = installation['project'] / 'release' / COMPANION
    path.write_bytes(b'keep different existing companion')
    assert_refused(installation, 'release file differs')
    assert path.read_bytes() == b'keep different existing companion'


@pytest.mark.parametrize('unsafe,wrong_version,message', [
    (True, False, 'Unsafe or unexpected runtime archive path'),
    ('chain', False, 'Runtime link resolves outside'),
    ('cycle', False, 'Runtime link cycle'),
    (False, True, 'Runtime project metadata differs'),
])
def test_runtime_preflight_failure_preserves_source(installation, unsafe, wrong_version, message):
    runtime_archive(installation['kit'], unsafe=unsafe, wrong_version=wrong_version)
    refresh_checksums(installation['kit'])
    assert_refused(installation, message)
    assert not (installation['project'] / 'runtime-ubuntu' / RUNTIME).exists()
    assert not (installation['project'] / 'escaped.txt').exists()
