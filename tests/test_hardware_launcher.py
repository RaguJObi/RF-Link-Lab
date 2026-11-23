"""Exercise hardware entry path handling with stubs; no board tools are run."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
if os.name == "nt":
    _git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    BASH = str(_git_bash) if _git_bash.is_file() else None
else:
    BASH = shutil.which("bash")


def bash_path(path):
    value = Path(path).as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def script(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\nset -Eeuo pipefail\n" + text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.mark.skipif(BASH is None, reason="Bash is unavailable")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="Launcher intentionally refuses root")
@pytest.mark.parametrize("layout", ["source", "portable"])
@pytest.mark.parametrize("override", ["default", "absolute", "relative"])
def test_hardware_entry_uses_prepared_tools_and_selected_serial(tmp_path, layout, override):
    project = tmp_path / "project with spaces"
    project.mkdir()
    (project / "PROJECT_CONTEXT.json").write_text(json.dumps({"version": "0.3.1"}))
    if layout == "portable":
        launch_dir = app = project
        default_tools = project / "hardware-tools" / "nuand-2025.10"
        default_tools.mkdir(parents=True)
    else:
        launch_dir = project / "deployment" / "hardware"
        launch_dir.mkdir(parents=True)
        app = project / "runtime-ubuntu" / "RF-Link-Lab-v0.3.1-Ubuntu22-x86_64"
        default_tools = project / "build" / "nuand-2025.10"
    launcher = launch_dir / "START-HARDWARE.sh"
    shutil.copyfile(ROOT / "deployment" / "hardware" / launcher.name, launcher)
    prepare_log, app_log = tmp_path / "prepare.log", tmp_path / "app.log"
    script(launch_dir / "PREPARE-MATCHED-BLADERF.sh",
           'printf "%s\\n" "$RF_LINK_LAB_PROJECT" "$RF_LINK_LAB_TOOLS" "$@" > "$TEST_PREPARE_LOG"\n')
    script(app / "START-RF-LINK-LAB.sh",
           ': >&9\n'  # Descriptor must survive the launcher's final exec.
           'printf "%s\\n" "$RF_LINK_LAB_SELECTED_SERIAL" "$(command -v bladeRF-cli)" '
           '"$LD_LIBRARY_PATH" "$@" > "$TEST_APP_LOG"\n')
    custom_tools = tmp_path / "custom tools"
    tools = default_tools if override == "default" else custom_tools
    for candidate in (default_tools, custom_tools):
        script(candidate / "install" / "bin" / "bladeRF-cli", 'exit 97\n')
    stubs = tmp_path / "stubs"
    # Git Bash lacks flock. This test checks path/argument forwarding and FD
    # inheritance; exclusion semantics remain the system flock's responsibility.
    script(stubs / "flock", '[[ $1 == -n && $2 == 9 ]]\n: >&9\n')
    script(stubs / "python3", 'printf "0.3.1\\n"\n')
    env = dict(os.environ, TEST_STUB_BIN=bash_path(stubs),
               TEST_PREPARE_LOG=bash_path(prepare_log), TEST_APP_LOG=bash_path(app_log))
    for name in ("RF_LINK_LAB_APP", "RF_LINK_LAB_TOOLS", "LD_LIBRARY_PATH"):
        env.pop(name, None)
    if override == "absolute":
        env["RF_LINK_LAB_TOOLS"] = bash_path(custom_tools)
    elif override == "relative":
        env["RF_LINK_LAB_TOOLS"] = "custom tools"
    selected = "0123456789ABCDEF0123456789ABCDEF"
    completed = subprocess.run(
        [BASH, "-c", 'export PATH="$TEST_STUB_BIN:$PATH"; exec bash "$@"',
         "test-hardware-entry", bash_path(launcher), "--serial", selected, "probe", "argument with spaces"],
        env=env, cwd=tmp_path, text=True, capture_output=True, timeout=15,
    )
    assert completed.returncode == 0, completed.stderr
    assert prepare_log.read_text().splitlines() == [
        bash_path(project), bash_path(tools), "--serial", selected.lower(),
    ]
    assert app_log.read_text().splitlines() == [
        selected.lower(), bash_path(tools / "install" / "bin" / "bladeRF-cli"),
        bash_path(tools / "install" / "lib"), "probe", "argument with spaces",
    ]
