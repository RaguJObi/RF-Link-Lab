"""Validate release launcher behavior without installing packages or using boards."""

import os
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deployment" / "ubuntu"


def bash_path():
    if os.name == "nt":
        git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
        if git_bash.is_file():
            return str(git_bash)
        return None
    return shutil.which("bash")


BASH = bash_path()
needs_bash = pytest.mark.skipif(BASH is None, reason="Bash is unavailable for script syntax/behavior checks")
PWSH = shutil.which("pwsh")
needs_pwsh = pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable for isolated transfer-helper checks")


def run_bash(script, *args, env=None):
    return subprocess.run(
        [BASH, str(script), *args], text=True, capture_output=True,
        timeout=10, env=env, check=False,
    )


@needs_bash
@pytest.mark.parametrize("name", ["SETUP-UBUNTU.sh", "START-RF-LINK-LAB.sh", "FLASH-STM32.sh"])
def test_ubuntu_scripts_have_valid_bash_syntax_and_lf(name):
    path = SCRIPTS / name
    assert b"\r" not in path.read_bytes(), "Linux launchers must retain LF endings"
    result = subprocess.run([BASH, "-n", str(path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@needs_bash
def test_setup_help_does_not_require_sudo_or_network():
    result = run_bash(SCRIPTS / "SETUP-UBUNTU.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Ubuntu 22.04" in result.stdout
    assert "Does not flash" in result.stdout


def test_setup_and_launcher_preserve_explicit_boundaries():
    setup = (SCRIPTS / "SETUP-UBUNTU.sh").read_text()
    launcher = (SCRIPTS / "START-RF-LINK-LAB.sh").read_text()
    assert '"${VERSION_ID:-}" != 22.04' in setup
    assert "ppa:nuandllc/bladerf" in setup
    assert "bladerf-fpga-hostedxa9" in setup
    assert setup.index("sudo apt-get install -y software-properties-common ca-certificates") < setup.index("sudo add-apt-repository -y universe")
    assert setup.index("sudo add-apt-repository -y universe") < setup.index("libxcb-cursor0")
    assert "usermod -a -G dialout,plugdev" in setup
    assert "--flash-firmware" not in setup
    assert "systemctl" not in setup
    assert "apt-get" not in launcher
    assert "pip install" not in launcher
    assert "FLASH-STM32" not in launcher
    assert 'exec "$runtime_python" -m rf_link_lab "$@"' in launcher


@needs_bash
def test_launcher_fails_with_actionable_message_when_bundle_is_incomplete(tmp_path):
    launcher = tmp_path / "START-RF-LINK-LAB.sh"
    shutil.copyfile(SCRIPTS / launcher.name, launcher)
    result = run_bash(launcher)
    assert result.returncode == 2
    assert "Extract the complete Ubuntu" in result.stderr


@needs_bash
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="GUI launcher intentionally refuses root")
def test_launcher_uses_packaged_runtime_with_writable_state_and_paths_with_spaces(tmp_path):
    bundle = tmp_path / "bundle with spaces"
    runtime = bundle / "runtime" / "python" / "bin"
    runtime.mkdir(parents=True)
    (bundle / "app" / "rf_link_lab").mkdir(parents=True)
    (bundle / "runtime" / "site-packages").mkdir(parents=True)
    python_stub = runtime / "python3.12"
    python_stub.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$PYTHONPATH" "$RF_LINK_LAB_RESOURCES" "$RF_LINK_LAB_HOME" "$@" > "$TEST_LAUNCH_LOG"\n',
        encoding="utf-8",
    )
    python_stub.chmod(0o755)
    launcher = bundle / "START-RF-LINK-LAB.sh"
    shutil.copyfile(SCRIPTS / launcher.name, launcher)
    log = tmp_path / "launch log.txt"
    state = tmp_path / "state with spaces"
    env = dict(os.environ, TEST_LAUNCH_LOG=log.as_posix(), RF_LINK_LAB_HOME=state.as_posix())
    result = run_bash(launcher, env=env)
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert "bundle with spaces/app:" in lines[0]
    assert lines[0].endswith("bundle with spaces/runtime/site-packages")
    assert lines[1].endswith("bundle with spaces")
    assert lines[2].endswith("state with spaces")
    assert lines[3:] == ["-m", "rf_link_lab", "gui"]
    assert state.is_dir()
    result = run_bash(launcher, "serial-demo", env=env)
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[3:] == ["-m", "rf_link_lab", "serial-demo"]


@needs_bash
def test_flash_refuses_unspecified_target_without_running_a_tool():
    result = run_bash(SCRIPTS / "FLASH-STM32.sh")
    assert result.returncode == 2
    assert "--serial" in result.stderr
    assert "NUCLEO-F446RE" in result.stderr


@needs_bash
@pytest.mark.parametrize("chip,expected", [("0x0421", 0), ("0x0413", 4)])
def test_flash_checks_selected_probe_and_chip_before_programming(tmp_path, chip, expected):
    bundle = tmp_path / "board bundle"
    (bundle / "firmware").mkdir(parents=True)
    (bundle / "firmware" / "rf_link_lab_f446re.bin").write_bytes(b"test image; not flashed to hardware")
    launcher = bundle / "FLASH-STM32.sh"
    shutil.copyfile(SCRIPTS / launcher.name, launcher)
    tools = tmp_path / "fake-tools"
    tools.mkdir()
    serial = "1234567890ABCDEF12345678"
    probe = tools / "st-info"
    probe.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "Found 1 stlink programmers" '
        f'" serial: {serial}" " chipid: {chip}"\n', encoding="utf-8",
    )
    flash = tools / "st-flash"
    flash.write_text(
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$@" > "$TEST_FLASH_LOG"\n', encoding="utf-8",
    )
    probe.chmod(0o755)
    flash.chmod(0o755)
    log = tmp_path / "flash log.txt"
    # MSYS accepts C:/ paths in PATH components, but a leading drive colon can
    # split a Unix PATH. /c/... is unambiguous inside Git Bash on Windows.
    tools_path = tools.as_posix()
    if os.name == "nt":
        tools_path = "/" + tools_path[0].lower() + tools_path[2:]
    env = dict(os.environ, PATH=tools_path + os.pathsep + os.environ.get("PATH", ""), TEST_FLASH_LOG=log.as_posix())
    if os.name == "nt":
        # Windows PATH is converted by Git Bash at startup; pass the fake tools
        # through a task-specific variable and prepend within the child shell.
        env["TEST_FAKE_TOOLS"] = tools_path
        command = 'export PATH="$TEST_FAKE_TOOLS:$PATH"; exec bash "$@"'
        result = subprocess.run(
            [BASH, "-c", command, "test-flash", str(launcher), "--serial", serial, "--board", "NUCLEO-F446RE"],
            env=env, capture_output=True, text=True, timeout=10,
        )
    else:
        result = run_bash(launcher, "--serial", serial, "--board", "NUCLEO-F446RE", env=env)
    assert result.returncode == expected, result.stderr
    if expected:
        assert not log.exists()
        assert "No programming command was run" in result.stderr
    else:
        arguments = log.read_text().splitlines()
        assert arguments[:4] == ["--serial", serial, "--reset", "write"]
        assert arguments[4].endswith("firmware/rf_link_lab_f446re.bin")
        assert arguments[5] == "0x08000000"


def _transfer_fixture(tmp_path, version="0.4.0"):
    """Tiny metadata fixtures; remote tools are always replaced by functions."""
    kit = tmp_path / "renamed kit with spaces"
    kit.mkdir()
    script = kit / "TRANSFER-TO-UBUNTU.ps1"
    shutil.copyfile(ROOT / "deployment/hardware" / script.name, script)
    archive = kit / f"RF-Link-Lab-v{version}-Ubuntu22-x86_64.tar.gz"
    archive.write_bytes(b"structural archive fixture; never extracted")
    validation = kit / f"UBUNTU-HARDWARE-VALIDATION-v{version}.json"
    validation.write_text(json.dumps({"schema": "rf-link-lab-ubuntu-hardware-release-v1", "version": version,
                                    "archive": archive.name, "bytes": archive.stat().st_size,
                                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}))
    (kit / "INSTALL-RF-LINK-LAB.sh").write_text("not executed in these tests")
    _transfer_manifest(kit, version)
    return kit, validation


def _transfer_manifest(kit, version="0.4.0"):
    files = [{"file": path.name, "bytes": path.stat().st_size,
              "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in kit.iterdir() if path.is_file() and path.name != "KIT-MANIFEST.json"]
    manifest = {"kit": "C:\\release\\transfer-v" + version + "-20261021", "files": files}
    (kit / "KIT-MANIFEST.json").write_text(json.dumps(manifest))


def _run_transfer_stubbed(kit, tmp_path):
    """Capture SSH/SCP inputs without opening a network connection."""
    calls = tmp_path / "stub-calls.jsonl"
    quoted_calls = str(calls).replace("'", "''")
    quoted_helper = str(kit / "TRANSFER-TO-UBUNTU.ps1").replace("'", "''")
    command = """
$ErrorActionPreference = 'Stop'
function global:ssh {
    $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((@($input) -join '')))
    [ordered]@{tool='ssh'; arguments=@($args); script=$text} | ConvertTo-Json -Compress | Add-Content -LiteralPath '__CALLS__'
    $global:LASTEXITCODE = 0
    Write-Output 'isolated SSH stub'
}
function global:scp {
    [ordered]@{tool='scp'; arguments=@($args)} | ConvertTo-Json -Compress | Add-Content -LiteralPath '__CALLS__'
    $global:LASTEXITCODE = 0
}
& '__HELPER__'
""".replace("__CALLS__", quoted_calls).replace("__HELPER__", quoted_helper)
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    result = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=30, check=False)
    rows = [json.loads(line) for line in calls.read_text(encoding="utf-8-sig").splitlines()] if calls.exists() else []
    return result, rows


@needs_pwsh
def test_first_install_transfer_uses_colocated_release_metadata_and_keeps_guards(tmp_path):
    kit, _ = _transfer_fixture(tmp_path, "0.9.7")
    result, calls = _run_transfer_stubbed(kit, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [call["tool"] for call in calls] == ["ssh", "scp", "ssh"]
    first, copy, install = calls
    destination = "/home/rfll/SDR/bladeRF/RF-Link-Lab-transfer-v0.9.7-20261021"
    assert destination in first["script"]
    assert copy["arguments"][-1] == "rfll@ubuntu-bench.local:" + destination
    assert "RF-Link-Lab-v0.9.7-Ubuntu22-x86_64" in install["script"]
    assert "v0.3.2" not in first["script"] + install["script"]
    assert '"$(hostname)" != ubuntu-bench' in first["script"]
    assert "EXISTING PROJECT: installation refused" in first["script"]
    for call in calls:
        assert "StrictHostKeyChecking=yes" in call["arguments"]
    for call in (first, install):
        assert call["arguments"][-1] == "base64 --decode --ignore-garbage | bash"
    assert "st-info --probe" in install["script"]
    # st-flash may appear only in the read-only tool inventory, never as an invoked command.
    flash_lines = [line for line in install["script"].splitlines() if "st-flash" in line]
    assert all("command -v" in line for line in flash_lines), flash_lines
    assert json.loads((kit / "transfer-status.json").read_text(encoding="utf-8-sig"))["phase"] == "complete"


@needs_pwsh
@pytest.mark.parametrize("fault", ["mixed_version", "tampered_archive", "unsafe_name", "extra_runtime", "kit_identity", "duplicate_entry"])
def test_first_install_transfer_rejects_invalid_kit_before_any_remote_call(tmp_path, fault):
    kit, validation = _transfer_fixture(tmp_path)
    manifest = json.loads((kit / "KIT-MANIFEST.json").read_text())
    if fault == "mixed_version":
        record = json.loads(validation.read_text())
        record["version"] = "0.3.2"
        validation.write_text(json.dumps(record))
        _transfer_manifest(kit)
    elif fault == "tampered_archive":
        (kit / "RF-Link-Lab-v0.4.0-Ubuntu22-x86_64.tar.gz").write_bytes(b"changed")
    elif fault == "extra_runtime":
        (kit / "RF-Link-Lab-v0.3.2-Ubuntu22-x86_64.tar.gz").write_bytes(b"old unlisted runtime")
    else:
        if fault == "unsafe_name":
            manifest["files"][0]["file"] = "../outside.json"
        elif fault == "kit_identity":
            manifest["kit"] = "C:\\release\\transfer-v0.4.0-20261021;unexpected"
        else:
            manifest["files"].append(manifest["files"][0])
        (kit / "KIT-MANIFEST.json").write_text(json.dumps(manifest))
    result, calls = _run_transfer_stubbed(kit, tmp_path)
    assert result.returncode != 0
    assert calls == []
    assert json.loads((kit / "transfer-status.json").read_text(encoding="utf-8-sig"))["phase"] == "failed"
