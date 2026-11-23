"""Drive our native panel offscreen, without OS input or actual RF hardware."""

import json
import os
import threading
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from rf_link_lab.conducted_ui import ConductedPanel
from rf_link_lab.conducted_waveform import load_tx_reference


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def release_native_panels(app, monkeypatch):
    monkeypatch.delenv('RF_LINK_LAB_SELECTED_SERIAL', raising=False)
    yield
    # These tests use processEvents(), not QApplication.exec(). Deliver native
    # deferred deletes explicitly, before Python interpreter finalization.
    for widget in app.topLevelWidgets():
        if isinstance(widget, ConductedPanel):
            assert widget.shutdown(), 'test left a finite worker running'
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def panel(snapshot=None):
    errors, results = [], []
    widget = ConductedPanel(lambda: snapshot or {}, results.append, errors.append)
    return widget, errors, results


def wait(app, widget, timeout=10):
    deadline = time.monotonic() + timeout
    while widget.is_busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    app.processEvents()
    assert not widget.is_busy(), 'finite test worker did not finish'


def fill_example_plan(widget):
    # These are calculation fixtures, not declarations about actual hardware.
    widget.serial.setText('a' * 32)
    widget.attenuator_label.setText('Fictitious stage for UI test')
    widget.attenuation.setText('50')
    widget.frequency_min.setText('1000000')
    widget.frequency_max.setText('6000000000')
    widget.power_rating.setText('1')
    widget.tx_power_bound.setText('20')
    widget.power_source.setText('Fictitious known conservative bound; offline UI unit test only.')
    widget.connection_checked.setChecked(True)
    widget.unused_loaded.setChecked(True)


def test_constructor_has_unknown_specs_and_never_opens_a_device(app, monkeypatch):
    import serial
    import rf_link_lab.bladerf_duplex as transport
    import rf_link_lab.hardware as hardware
    monkeypatch.setattr(serial, 'Serial', lambda *a, **kw: pytest.fail('constructor opened serial'))
    monkeypatch.setattr(hardware, 'probe_bladerf', lambda: pytest.fail('constructor probed RF'))
    monkeypatch.setattr(transport, 'run_finite_duplex', lambda *a, **kw: pytest.fail('constructor opened RF'))
    widget, errors, _ = panel()
    assert not errors and widget.shutdown()
    assert not widget.serial.text() and not widget.attenuation.text() and not widget.tx_power_bound.text()
    assert not widget.connection_checked.isChecked() and not widget.unused_loaded.isChecked()
    assert not widget.live_btn.isEnabled() and not widget.rehearsal_btn.isEnabled()
    assert widget.sample_rate.value() == 1_000_000 and widget.symbol_rate.value() == 50_000
    assert widget.frequency.value() == 915_000_000 and widget.tx_scale.value() == .25
    widget.close()


def test_prepare_offline_exact_payload_and_preserve_existing_folder(app, tmp_path):
    widget, errors, _ = panel()
    reference = widget.prepare_waveform(tmp_path / 'prepared')
    loaded = load_tx_reference(reference)
    assert loaded['manifest']['frames_expected'] == 12
    assert widget.payload_source['source'] == 'offline_example'
    assert json.loads((reference.parent / 'payload-source.json').read_text(encoding='utf-8'))['source'] == 'offline_example'
    assert 'NO RF TRANSMISSION' in widget.status.text()
    assert widget.rehearsal_btn.isEnabled() and not widget.live_btn.isEnabled()
    original = reference.read_bytes()
    assert widget.prepare_waveform(tmp_path / 'prepared') is None
    assert errors and reference.read_bytes() == original
    assert not widget.rehearsal_btn.isEnabled()
    widget.close()


def test_hardware_launcher_prefills_and_binds_selected_serial(app, monkeypatch):
    selected = '0123456789ABCDEF0123456789ABCDEF'
    monkeypatch.setenv('RF_LINK_LAB_SELECTED_SERIAL', selected)
    widget, errors, _ = panel()
    assert not errors
    assert widget.serial.text() == selected.lower()
    assert widget.serial.isReadOnly()
    assert 'hardware launcher' in widget.serial.toolTip()
    assert not widget.connection_checked.isChecked() and not widget.unused_loaded.isChecked()
    widget.close()


def test_serial_preparation_filters_source_and_current_session(app, tmp_path):
    snapshot = {'input_source': 'serial', 'session_id': 7, 'port': '/dev/ttyACM0', 'recent': [
        {'sequence': 1, 'uptime_ms': 100, 'button': True, 'source': 'offline_feed', 'session_id': 7},
        {'sequence': 2, 'uptime_ms': 200, 'button': False, 'source': 'serial', 'session_id': 6},
        {'sequence': 3, 'uptime_ms': 300, 'button': True, 'source': 'serial', 'session_id': 7},
        {'sequence': 4, 'uptime_ms': 400, 'button': False, 'source': 'serial', 'session_id': 7},
    ]}
    widget, errors, _ = panel(snapshot)
    widget.source.setCurrentIndex(1)
    reference = widget.prepare_waveform(tmp_path / 'serial-prepared')
    assert not errors
    assert [row['sequence'] for row in load_tx_reference(reference)['manifest']['payload']] == [3, 4]
    assert widget.payload_source['source'] == 'current_serial'
    assert widget.payload_source['session_id'] == 7
    snapshot['recent'][-1]['sequence'] = 999
    assert widget.payload_source['selected_rows'][-1]['sequence'] == 4
    widget.close()


@pytest.mark.parametrize('snapshot', [
    {}, {'input_source': 'offline_feed', 'session_id': 1, 'recent': []},
    {'input_source': 'serial', 'session_id': 1, 'recent': []},
    {'input_source': 'serial', 'session_id': 1, 'recent': [
        {'sequence': 1, 'uptime_ms': 100, 'button': True, 'source': 'serial', 'session_id': 2}]},
])
def test_no_current_physical_serial_rows_cannot_become_nucleo_payload(app, tmp_path, snapshot):
    widget, errors, _ = panel(snapshot)
    widget.source.setCurrentIndex(1)
    assert widget.prepare_waveform(tmp_path / 'not-created') is None
    assert errors and not (tmp_path / 'not-created').exists()
    widget.close()


def test_unknown_plan_blocks_programmatic_live_start_before_runner(app, tmp_path, monkeypatch):
    import rf_link_lab.conducted as runner
    monkeypatch.setattr(runner, 'run_conducted_experiment', lambda *a, **kw: pytest.fail('invalid plan reached runner'))
    widget, errors, _ = panel()
    widget.prepare_waveform(tmp_path / 'prepared')
    assert not widget.start_live(tmp_path / 'must-not-exist')
    assert errors and not widget.is_busy()
    assert not (tmp_path / 'must-not-exist').exists()
    assert not widget.live_btn.isEnabled()
    widget.close()


def test_valid_plan_enables_live_and_any_setting_change_invalidates_it(app, tmp_path):
    widget, errors, _ = panel()
    widget.prepare_waveform(tmp_path / 'prepared')
    fill_example_plan(widget)
    assert widget.validate_plan() is not None
    assert widget.live_btn.isEnabled()
    widget.tx_gain.setValue(-21)
    assert not widget.live_btn.isEnabled()
    assert widget.validate_plan() is not None
    widget.tx_scale.setValue(.3)
    assert not widget.live_btn.isEnabled() and not widget.rehearsal_btn.isEnabled()
    assert not errors
    widget.close()


def test_loaded_multistage_plan_matches_selected_settings_and_preserves_stricter_ceiling(app, tmp_path):
    widget, errors, _ = panel()
    widget.prepare_waveform(tmp_path / 'prepared')
    fill_example_plan(widget)
    plan = widget._connection_plan()
    stage = plan['attenuators'][0]
    stage['attenuation_db'] = 20
    plan['attenuators'].append(dict(stage, label='Second fictitious stage', attenuation_db=30, power_rating_w=.01))
    plan['rx_working_ceiling_dbm'] = -30
    path = tmp_path / 'plan.json'; path.write_text(json.dumps(plan), encoding='utf-8')
    assert widget.load_plan(path) == plan
    assert not widget.attenuation.isEnabled()
    result = widget.validate_plan()
    assert result['calculations']['rx_working_ceiling_dbm'] == -30
    assert widget.live_btn.isEnabled()
    widget.frequency.setValue(916_000_000)
    assert widget.validate_plan() is None and not widget.live_btn.isEnabled()
    widget.use_form_plan()
    assert widget.attenuation.isEnabled()
    assert errors and 'match' in errors[-1]
    widget.close()


def test_software_rehearsal_produces_portable_results_without_plan_or_hardware(app, tmp_path, monkeypatch):
    import serial
    import rf_link_lab.bladerf_duplex as transport
    from rf_link_lab.conducted import verify_conducted_experiment
    monkeypatch.setattr(serial, 'Serial', lambda *a, **kw: pytest.fail('rehearsal opened serial'))
    monkeypatch.setattr(transport, 'run_finite_duplex', lambda *a, **kw: pytest.fail('rehearsal opened RF'))
    widget, errors, results = panel()
    activity, completed = [], []
    widget.activity_changed.connect(activity.append)
    widget.experiment_completed.connect(completed.append)
    widget.prepare_waveform(tmp_path / 'prepared')
    assert widget.start_rehearsal(tmp_path / 'experiment')
    wait(app, widget)
    assert not errors and activity == [True, False] and len(completed) == 1
    assert widget.last_result['mode'] == 'rehearsal'
    assert widget.last_result['status'] == 'decoded'
    assert 'no RF transmission' in widget.status.text()
    assert 'rx.sc16q11' in widget.output.toPlainText()
    assert verify_conducted_experiment(tmp_path / 'experiment')['status'] == 'verified'
    assert widget.decode_btn.isEnabled() and widget.report_btn.isEnabled()
    widget.show_decoded()
    assert len(results) == 1 and results[0].metadata['diagnosis_allowed'] is False
    assert results[0].metrics['known_payload_complete']
    assert widget.shutdown()
    widget.close()


def test_stop_and_shutdown_cancel_worker_and_wait_for_final_cleanup(app, tmp_path, monkeypatch):
    import rf_link_lab.conducted as runner
    entered = threading.Event()
    release = threading.Event()
    captured = {}
    def mock_run(path, **kwargs):
        captured.update(kwargs); entered.set()
        assert release.wait(3), 'test did not release finite worker'
        return {'mode': kwargs['mode'], 'status': 'cancelled', 'cleanup_confirmed': True,
                'metrics': {}, 'experiment_manifest': str(path / 'experiment.json'),
                'bench_manifest': None, 'receiver_manifest': None, 'error': 'Test cancellation'}
    monkeypatch.setattr(runner, 'run_conducted_experiment', mock_run)
    widget, errors, _ = panel()
    activity = []
    widget.activity_changed.connect(activity.append)
    widget.prepare_waveform(tmp_path / 'prepared')
    assert widget.start_rehearsal(tmp_path / 'experiment')
    assert entered.wait(3)
    assert widget.is_busy() and not widget.shutdown()
    assert captured['cancel_event'].is_set() and not widget.stop_btn.isEnabled()
    assert not widget.start_rehearsal(tmp_path / 'another')
    release.set(); wait(app, widget)
    assert activity == [True, False] and widget.shutdown()
    assert widget.last_result['status'] == 'cancelled'
    assert errors == ['A finite experiment is already running.']
    widget.close()


def test_live_failure_with_unconfirmed_cleanup_requires_operator_review_and_fresh_plan(app, tmp_path, monkeypatch):
    import rf_link_lab.conducted as runner
    calls = []
    def mock_run(path, **kwargs):
        calls.append(kwargs)
        return {'mode': 'live', 'status': 'failed', 'cleanup_confirmed': False,
                'metrics': {}, 'experiment_manifest': str(path / 'experiment.json'),
                'bench_manifest': None, 'receiver_manifest': None, 'error': 'Mock unconfirmed stop'}
    monkeypatch.setattr(runner, 'run_conducted_experiment', mock_run)
    widget, errors, _ = panel()
    widget.prepare_waveform(tmp_path / 'prepared'); fill_example_plan(widget)
    assert widget.validate_plan() is not None
    # Programmatic mutations bypassing change signals still get revalidated.
    widget.power_rating.blockSignals(True); widget.power_rating.setText('.001'); widget.power_rating.blockSignals(False)
    assert not widget.start_live(tmp_path / 'rejected') and not calls
    widget.power_rating.setText('1')
    assert widget.start_live(tmp_path / 'first')
    wait(app, widget)
    assert len(calls) == 1 and calls[0]['payload_source']['source'] == 'offline_example'
    assert 'NOT CONFIRMED' in widget.status.text()
    assert not widget.live_btn.isEnabled()
    assert not widget.start_live(tmp_path / 'blocked') and len(calls) == 1
    assert not widget.record_operator_review()
    widget.review_checked.setChecked(True)
    assert widget.record_operator_review()
    assert not widget.connection_checked.isChecked() and not widget.unused_loaded.isChecked()
    assert not widget.live_btn.isEnabled()
    fill_example_plan(widget)
    assert widget.validate_plan() is not None and widget.live_btn.isEnabled()
    widget.close()


def test_live_worker_exception_is_visible_and_cannot_automatically_retry(app, tmp_path, monkeypatch):
    import rf_link_lab.conducted as runner
    def fail(*args, **kwargs): raise RuntimeError('test transport outcome unavailable')
    monkeypatch.setattr(runner, 'run_conducted_experiment', fail)
    widget, errors, _ = panel()
    widget.prepare_waveform(tmp_path / 'prepared'); fill_example_plan(widget)
    assert widget.start_live(tmp_path / 'experiment')
    wait(app, widget)
    assert errors and 'test transport outcome unavailable' in errors[-1]
    assert 'NOT CONFIRMED' in widget.status.text() and not widget.live_btn.isEnabled()
    assert widget.shutdown()
    widget.close()
