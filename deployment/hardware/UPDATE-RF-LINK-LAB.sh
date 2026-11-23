#!/usr/bin/env bash
# Update an explicitly selected existing checkout from a local transfer kit.
# No network, package installation, board tools, application launch or cleanup.
set -Eeuo pipefail
umask 077
project= kit=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while [[ $# -gt 0 ]]; do
    [[ $# -ge 2 ]] || { echo 'Every option requires a value.' >&2; exit 2; }
    case "$1" in
        --project) project=$2 ;;
        --kit) kit=$2 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift 2
done
if [[ $EUID -eq 0 || $project != /* || ! -d $project || -L $project || ! -d $project/.git ]]; then
    echo 'Usage as your normal desktop user: bash UPDATE-RF-LINK-LAB.sh --project /absolute/existing/RF-Link-Lab [--kit /absolute/transfer-kit]' >&2
    exit 2
fi
for tool in python3 git flock; do
    command -v "$tool" >/dev/null || { echo "Required local tool is missing: $tool" >&2; exit 2; }
done
project=$(cd -- "$project" && pwd)
[[ ! -L $project/build ]] || { echo 'The project build folder must not be a symbolic link.' >&2; exit 2; }
mkdir -p -- "$project/build"
exec 9>"$project/build/rf-link-lab-hardware.lock"
flock -n 9 || { echo 'Another hardware session or update is running. Close it before updating.' >&2; exit 2; }
exec python3 - "$project" "$kit" <<'PY'
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

project = Path(sys.argv[1]).resolve(strict=True)
kit = Path(sys.argv[2]).resolve(strict=True)
stage = None
phase = 'preflight'
before = pinned = version = None
environment = dict(os.environ)
for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY', 'GIT_COMMON_DIR'):
    environment.pop(key, None)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(folder, *arguments, check=True):
    return subprocess.run(
        ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'submodule.recurse=false',
         '-C', str(folder), *arguments], env=environment, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check,
    )


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def ordinary(path):
    require(path.is_file() and not path.is_symlink(), 'Missing regular file: ' + str(path))
    return path


def checksum_line(line):
    match = re.fullmatch(r'([0-9a-fA-F]{64}) [ *]([A-Za-z0-9][A-Za-z0-9._+\-]*)', line)
    require(match is not None, 'Unsafe or malformed checksum record.')
    return match.group(2), match.group(1).lower()


def status(code, error=None):
    if stage is not None:
        value = {'schema': 'rf-link-lab.existing-install-update.v1', 'phase': phase,
                 'exit_code': code, 'project': str(project), 'kit': str(kit),
                 'previous_commit': before, 'source_commit': pinned, 'version': version,
                 'updated_utc': datetime.now(timezone.utc).isoformat(), 'error': error,
                 'scope': 'Local source/runtime update only; no hardware operation or application launch.'}
        (stage / 'update.json').write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')


try:
    require(project != Path(project.anchor), 'The filesystem root cannot be an update target.')
    require((project / '.git').is_dir() and not (project / '.git').is_symlink(),
            'Select an ordinary existing Git checkout, not a linked worktree.')
    require(Path(git(project, 'rev-parse', '--show-toplevel').stdout.strip()).resolve() == project,
            'The selected folder must be the Git project root.')
    require(git(project, 'branch', '--show-current').stdout.strip() == 'main',
            'The existing project must be on main; no branch switching is performed.')
    require(not git(project, 'status', '--porcelain', '--untracked-files=all').stdout.strip(),
            'The existing Git checkout is not clean. Preserve or commit local source changes before updating.')
    before = git(project, 'rev-parse', 'HEAD').stdout.strip()
    context = json.loads(ordinary(project / 'PROJECT_CONTEXT.json').read_text(encoding='utf-8'))
    require(context.get('project') == 'RF Link Lab', 'The target is not an RF Link Lab installation.')
    checksums = {}
    for line in ordinary(kit / 'SHA256SUMS').read_text(encoding='ascii').splitlines():
        if not line:
            continue
        name, expected = checksum_line(line)
        require(name not in checksums, 'Duplicate kit checksum: ' + name)
        require(digest(ordinary(kit / name)) == expected, 'Kit checksum mismatch: ' + name)
        checksums[name] = expected
    archives = list(kit.glob('RF-Link-Lab-v*-Ubuntu22-x86_64.tar.gz'))
    require(len(archives) == 1, 'The kit must contain exactly one Ubuntu runtime archive.')
    archive = ordinary(archives[0])
    match = re.fullmatch(r'RF-Link-Lab-v([0-9]+\.[0-9]+\.[0-9]+)-Ubuntu22-x86_64\.tar\.gz', archive.name)
    require(match is not None, 'Unexpected Ubuntu runtime archive name.')
    version = match.group(1)
    runtime_name = archive.name[:-7]
    companions = list(kit.glob('Nuand-bladeRF-*-corresponding-source.tar.gz'))
    require(len(companions) == 1, 'The kit must contain exactly one Nuand corresponding-source companion.')
    companion = ordinary(companions[0])
    bundle = kit / 'RF-Link-Lab-history.bundle'
    required = {archive.name, archive.name + '.sha256', companion.name, companion.name + '.sha256',
                bundle.name, 'SOURCE-COMMIT.txt'}
    require(required <= checksums.keys(), 'The kit checksum inventory omits a required update input.')
    for item in (archive, companion):
        records = (kit / (item.name + '.sha256')).read_text(encoding='ascii').splitlines()
        require(len(records) == 1 and checksum_line(records[0]) == (item.name, checksums[item.name]),
                'The archive checksum sidecar disagrees with the kit: ' + item.name)
    pinned = (kit / 'SOURCE-COMMIT.txt').read_text(encoding='ascii').strip()
    require(re.fullmatch(r'[0-9a-fA-F]{40}', pinned) is not None, 'The pinned source commit is invalid.')
    pinned = pinned.lower()
    heads = git(project, 'bundle', 'list-heads', str(bundle), 'refs/heads/main').stdout.splitlines()
    require(heads == [pinned + ' refs/heads/main'], 'The bundle main commit differs from SOURCE-COMMIT.txt.')
    git(project, 'bundle', 'verify', str(bundle))
    for name in ('runtime-ubuntu', 'release'):
        parent = project / name
        require(not parent.is_symlink() and (not parent.exists() or parent.is_dir()),
                'The installation folder must be an ordinary directory: ' + str(parent))
    destination = project / 'runtime-ubuntu' / runtime_name
    require(not destination.exists() and not destination.is_symlink(),
            'Existing same-version runtime preserved; choose a newer release or inspect it explicitly: ' + str(destination))
    release_inputs = [archive, kit / (archive.name + '.sha256'), companion, kit / (companion.name + '.sha256')]
    for item in release_inputs:
        target = project / 'release' / item.name
        if target.exists() or target.is_symlink():
            require(not target.is_symlink() and target.is_file() and digest(target) == checksums[item.name],
                    'Existing release file differs and is preserved: ' + str(target))

    updates = project / '.git' / 'rf-link-lab-updates'
    require(not updates.is_symlink(), 'The update staging folder must not be a symbolic link.')
    updates.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='v' + version + '-', dir=updates))
    print('Staging update: ' + str(stage), flush=True)
    phase = 'validating_staged_source_and_runtime'
    staged_source = stage / 'source'
    git(project, 'clone', '--no-checkout', '--single-branch', '--branch', 'main',
        '--origin', 'update-kit', str(bundle), str(staged_source))
    require(git(staged_source, 'rev-parse', 'HEAD').stdout.strip() == pinned,
            'The staged source commit does not match the pinned commit.')
    require(git(staged_source, 'merge-base', '--is-ancestor', before, pinned, check=False).returncode == 0,
            'The pinned release is not a fast-forward of the installed source. Existing source is preserved.')
    candidate_context = json.loads(git(staged_source, 'show', pinned + ':PROJECT_CONTEXT.json').stdout)
    require(candidate_context.get('project') == 'RF Link Lab' and candidate_context.get('version') == version,
            'Pinned source and Ubuntu runtime versions disagree.')
    changed = git(staged_source, 'diff', '--name-only', '-z', before, pinned).stdout.split('\0')
    require(not any(name.split('/')[0] in ('artifacts', 'runtime-ubuntu', 'release', 'build') for name in changed if name),
            'This source update would change preserved artifact/runtime/release paths; inspect it explicitly.')

    extracted = stage / 'runtime'
    extracted.mkdir()
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        member_modes = {member.name.rstrip('/'): member.mode for member in members}
        names, links, symlinks = set(), set(), {}
        for member in members:
            path = PurePosixPath(member.name)
            require(path.parts and not path.is_absolute() and '..' not in path.parts
                    and path.parts[0] == runtime_name and '\\' not in member.name,
                    'Unsafe or unexpected runtime archive path: ' + member.name)
            require(member.isfile() or member.isdir() or member.issym() or member.islnk(),
                    'Unsupported runtime archive member: ' + member.name)
            require(member.name.rstrip('/') not in names, 'Duplicate runtime archive member: ' + member.name)
            names.add(member.name.rstrip('/'))
            if member.issym() or member.islnk():
                require(member.linkname and not PurePosixPath(member.linkname).is_absolute()
                        and '\\' not in member.linkname, 'Unsafe runtime link: ' + member.name)
                base = path.parent.as_posix() if member.issym() else ''
                target = posixpath.normpath(posixpath.join(base, member.linkname))
                require(PurePosixPath(target).parts[0] == runtime_name,
                        'Runtime link escapes its root: ' + member.name)
                links.add(path.as_posix())
                if member.issym():
                    symlinks[path.as_posix()] = member.linkname
        for name in symlinks:
            pending, resolved, expansions = list(PurePosixPath(name).parts), [], 0
            while pending:
                part = pending.pop(0)
                if part == '.':
                    continue
                if part == '..':
                    require(resolved, 'Runtime link resolves outside its root: ' + name)
                    resolved.pop()
                    continue
                resolved.append(part)
                target = symlinks.get('/'.join(resolved))
                if target is not None:
                    expansions += 1
                    require(expansions <= 40, 'Runtime link cycle or excessive chain: ' + name)
                    resolved.pop()
                    pending = list(PurePosixPath(target).parts) + pending
            require(resolved and resolved[0] == runtime_name,
                    'Runtime link resolves outside its root: ' + name)
        for member in members:
            require(not any(parent.as_posix() in links for parent in PurePosixPath(member.name).parents),
                    'Runtime archive writes through a link: ' + member.name)
            if member.islnk():
                target = posixpath.normpath(member.linkname)
                require(target in names and target not in links, 'Unsafe runtime hard-link target: ' + member.name)
        source.extractall(extracted)
    staged_runtime = extracted / runtime_name
    runtime_context = json.loads(ordinary(staged_runtime / 'PROJECT_CONTEXT.json').read_text(encoding='utf-8'))
    require(runtime_context.get('project') == 'RF Link Lab' and runtime_context.get('version') == version,
            'Runtime project metadata differs from the pinned release.')
    for name in ('START-RF-LINK-LAB.sh', 'START-HARDWARE.sh', 'PREPARE-MATCHED-BLADERF.sh', 'RUN-CONNECTED-BENCH.sh'):
        path = ordinary(staged_runtime / name)
        require(member_modes[runtime_name + '/' + name] & 0o111
                and path.read_bytes().startswith(b'#!/usr/bin/env bash\n'),
                'A runtime launcher is missing executable mode or its Bash header: ' + name)
    python = ordinary(staged_runtime / 'runtime/python/bin/python3.12')
    with python.open('rb') as stream:
        elf = stream.read(20)
    require(len(elf) == 20 and elf[:6] == b'\x7fELF\x02\x01' and int.from_bytes(elf[18:20], 'little') == 62
            and member_modes[runtime_name + '/runtime/python/bin/python3.12'] & 0o111,
            'The staged Python runtime is not executable ELF64 x86_64.')
    require((staged_runtime / 'runtime/site-packages').is_dir(), 'The staged dependency directory is missing.')
    application = ast.parse(ordinary(staged_runtime / 'app/rf_link_lab/__init__.py').read_text(encoding='utf-8'))
    versions = [node.value.value for node in application.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets)
                and isinstance(node.value, ast.Constant)]
    require(versions == [version], 'The staged application version differs from the archive.')
    build_manifest = json.loads(ordinary(staged_runtime / 'BUILD-MANIFEST.json').read_text(encoding='utf-8'))
    require(build_manifest.get('project_version') == version, 'The staged build manifest version disagrees.')
    corresponding = json.loads(ordinary(staged_runtime / 'CORRESPONDING-SOURCE.json').read_text(encoding='utf-8'))
    require(corresponding.get('file') == companion.name and corresponding.get('sha256') == checksums[companion.name],
            'The runtime corresponding-source declaration differs from the supplied companion.')
    staged_release = stage / 'release'
    staged_release.mkdir()
    for item in release_inputs:
        shutil.copyfile(item, staged_release / item.name)
        require(digest(staged_release / item.name) == checksums[item.name], 'Staged release copy failed verification.')

    require(git(project, 'rev-parse', 'HEAD').stdout.strip() == before
            and not git(project, 'status', '--porcelain', '--untracked-files=all').stdout.strip(),
            'The installed source changed during staging; update refused.')
    require(not destination.exists() and not destination.is_symlink(), 'A same-version runtime appeared during staging; it is preserved.')
    phase = 'fast_forwarding_source'
    status(0)
    git(project, 'fetch', '--no-tags', str(bundle), 'refs/heads/main')
    require(git(project, 'rev-parse', 'FETCH_HEAD').stdout.strip() == pinned, 'Fetched source differs from the pinned commit.')
    git(project, 'merge', '--ff-only', '--no-edit', '--no-overwrite-ignore', pinned)
    phase = 'installing_runtime_and_release_files'
    (project / 'runtime-ubuntu').mkdir(exist_ok=True)
    (project / 'release').mkdir(exist_ok=True)
    # Exclusive directory creation refuses an existing same-version runtime.
    # copytree does not merge or overwrite an existing destination.
    shutil.copytree(staged_runtime, destination, symlinks=True)
    for item in release_inputs:
        target = project / 'release' / item.name
        if target.exists() or target.is_symlink():
            require(not target.is_symlink() and target.is_file() and digest(target) == checksums[item.name],
                    'A release file changed during staging; existing file preserved: ' + str(target))
        else:
            with (staged_release / item.name).open('rb') as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    require(git(project, 'rev-parse', 'HEAD').stdout.strip() == pinned, 'Source fast-forward did not reach the pinned release.')
    phase = 'complete'
    status(0)
    print('Updated source: ' + pinned)
    print('Installed runtime: ' + str(destination))
    print('Daily helper: ' + str(destination / 'RUN-CONNECTED-BENCH.sh'))
    print('Update record: ' + str(stage / 'update.json'))
    print('Older runtimes, artifacts and release files were preserved. No application or hardware operation was started.')
except Exception as error:
    try:
        status(1, str(error))
    except Exception:
        pass
    print('Update stopped: ' + str(error), file=sys.stderr)
    if stage is not None:
        print('Staging and update record preserved: ' + str(stage), file=sys.stderr)
    sys.exit(1)
PY
