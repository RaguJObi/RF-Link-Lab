"""Local diagnostics and deterministic software smoke tests; no device opens."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import sys
from uuid import uuid4

from .paths import resource_root,state_root
from . import __version__


def doctor():
    checks={}
    for package in ['numpy','scipy','PySide6-Essentials','scikit-learn','pyserial']:
        try:checks[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:checks[package]='not in package metadata; may be embedded'
    resources=resource_root();firmware=next((p for p in [resources/'firmware/rf_link_lab_f446re.bin',resources/'artifacts/firmware/rf_link_lab_f446re.bin'] if p.is_file()),None)
    result={'schema':'rf-link-lab-doctor-v1','application_version':__version__,'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'os':platform.platform(),'architecture':platform.machine(),'python':platform.python_version(),
            'frozen_executable':bool(getattr(sys,'frozen',False)),'resources':str(resources),'session_directory':str(state_root()),
            'packages':checks,'bladerf_cli':shutil.which('bladeRF-cli'),'st_flash':shutil.which('st-flash'),
            'firmware_binary':str(firmware) if firmware else None,'firmware_sha256':hashlib.sha256(firmware.read_bytes()).hexdigest() if firmware else None,
            'device_opened':False,'rf_transmission_enabled':False,
            'finite_tx_capability':'explicit Linux-only conducted workflow; requires operator connection/power plan',
            'stdbuf':shutil.which('stdbuf'),'hardware_readiness':'not established by local checks'}
    if sys.platform.startswith('linux'):
        result['linux_groups']=[]
        import grp
        result['linux_groups']=[grp.getgrgid(gid).gr_name for gid in os.getgroups()]
        result['permissions_hint']='dialout for ST-LINK serial; plugdev and Nuand udev rules for bladeRF. Sign out/in after one-time setup.'
    return result


def self_test(output: Path | None=None,with_ui: bool=False):
    """Use public APIs, including the independent receiver, in the built bundle."""
    from .protocol import Telemetry,encode_telemetry,decode_telemetry
    from .waveform import generate_waveform
    from .receiver import decode_iq
    from .serial_link import demo_serial_session
    message=Telemetry(123,45600,True)
    decoded=decode_telemetry(encode_telemetry(message))
    wave=generate_waveform([message])
    receiver=decode_iq(wave.tx_iq,wave.metadata)
    session=demo_serial_session()
    checks={'telemetry_roundtrip':decoded==message,'independent_iq_decode':receiver.metrics['packets_ok']==1,
            'missing_reference_ber_unavailable':receiver.metrics.get('ber') is None,
            'serial_crc_recovery':session['packets_ok']==6 and session['crc_errors']==1}
    resources=resource_root();model=resources/'artifacts/reference/model/model.json'
    if model.is_file():
        from .diagnostics import load_model,diagnose
        from .simulation import SimulationConfig,run_simulation
        result=run_simulation(SimulationConfig())
        inference=diagnose(result.features,load_model(model))
        checks['bundled_model_inference']=inference['label'] in ('healthy','unknown')
    else:checks['bundled_model_inference']=False
    from .bench import demo_bench_session,verify_bench_session
    from .serial_logging import replay_uart_recording
    evidence_root=Path(output).parent/'self-tests' if output else state_root()/'artifacts/self-tests'
    evidence_root.mkdir(parents=True,exist_ok=True)
    bench_manifest=demo_bench_session(evidence_root/f'offline-bench-{uuid4().hex[:12]}')
    bench_verified=verify_bench_session(bench_manifest)
    uart_replay=replay_uart_recording(Path(bench_manifest).parent/'uart')
    checks['persistent_uart_replay']=uart_replay['matches'] and uart_replay['replayed_summary']['crc_errors']==1
    checks['portable_bench_report']=bench_verified['status']=='verified' and bench_verified['evidence_count']==2
    from .conducted import example_payload,run_conducted_experiment,verify_conducted_experiment
    from .conducted_waveform import prepare_tx_waveform
    tx=prepare_tx_waveform(example_payload(),evidence_root/f'tx-{uuid4().hex[:12]}')
    experiment=run_conducted_experiment(evidence_root/f'link-{uuid4().hex[:12]}',tx_reference_path=tx)
    checks['finite_known_payload_rehearsal']=(experiment['status']=='decoded'
        and experiment['metrics'].get('known_payload_frames_matched')==12
        and experiment['metrics'].get('compared_bit_error_rate')==0)
    verified=verify_conducted_experiment(Path(experiment['experiment_manifest']))
    checks['portable_link_experiment']=(verified['status']=='verified'
        and verified['summary']['tx_start_requested'] is False
        and verify_bench_session(Path(experiment['bench_manifest']))['evidence_count']==3)
    if with_ui:
        from PySide6.QtWidgets import QApplication
        from .ui import Window
        app=QApplication.instance() or QApplication([])
        window=Window();window.show();app.processEvents();window.run_current()
        checks['native_widgets']=window.result.metrics['packets_ok']==12
        window.hardware_panel.start_bench(evidence_root/f'gui-bench-{uuid4().hex[:12]}')
        window.hardware_panel.serial_demo()
        checks['gui_serial_demo']=window.hardware_panel.session.snapshot()['packets_ok']==6
        gui_manifest=window.hardware_panel.finish_bench()
        checks['gui_bench_recording']=gui_manifest is not None and verify_bench_session(gui_manifest)['evidence_count']==1
        window.select_page(4);app.processEvents()
        window.hardware_panel.tabs.setCurrentIndex(3);app.processEvents()
        checks['finite_link_controls']=window.hardware_panel.tabs.count()==5 and not window.hardware_panel.conducted_panel.is_busy()
        if output:
            screenshot=Path(output).with_suffix('.png');screenshot.parent.mkdir(parents=True,exist_ok=True)
            checks['gui_screenshot_saved']=window.grab().save(str(screenshot))
        window.close();app.processEvents()
    report={'schema':'rf-link-lab-self-test-v1','version':__version__,'passed':all(checks.values()),'checks':checks,
            'offline_bench_manifest':str(bench_manifest),
            'offline_link_manifest':experiment['experiment_manifest'],
            'environment':doctor(),'scope':'Software execution only. No serial/USB device opened; no RF transmission.'}
    if output:
        output=Path(output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
