"""Explicit saved-file UI actions; no boards, ports, network or real model jobs."""
import copy
import os
import sys
import threading
import time
import types

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QWidget

from rf_link_lab.hardware_review_ui import HardwareReviewPanel, _ACTIVE_WORKERS


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def release_panels(app):
    yield
    for widget in app.topLevelWidgets():
        if isinstance(widget, HardwareReviewPanel):
            wait(app, widget)
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def wait(app, widget, timeout=5):
    deadline = time.monotonic() + timeout
    while widget.is_busy() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.003)
    app.processEvents()
    assert not widget.is_busy(), 'saved-file worker did not finish'


def module(monkeypatch, name, **functions):
    result = types.ModuleType('rf_link_lab.' + name)
    for key, function in functions.items():
        setattr(result, key, function)
    monkeypatch.setitem(sys.modules, result.__name__, result)
    return result


def reviewed():
    return {
        'schema': 'test-review',
        'source': {'experiment_path': 'physical/experiment.json', 'raw_rx_sha256': 'a' * 64,
                   'reprocessed': True},
        'outcome': {'experiment_status': 'capture_without_complete_payload',
                    'receiver_status': 'decoded', 'recorded_known_payload_complete': False,
                    'selected_known_payload_complete': True, 'physical_tx_requested': True,
                    'new_rf_transmission': False},
        'receiver_metrics': {'known_payload_frames_matched': 12, 'ber': 0},
        'findings': [{'code': 'recovered', 'title': '<operator supplied title>',
                      'kind': 'hypothesis', 'severity': 'info',
                      'evidence': {'original': 5, 'selected': 12},
                      'next_measurement': 'Repeat a fresh finite physical recording.'}],
        'limitations': ['Saved recording only; no new RF.'],
    }


def panel():
    errors = []
    return HardwareReviewPanel(errors.append), errors


def test_construction_is_passive_and_operator_choices_are_unknown(app, monkeypatch):
    import rf_link_lab.hardware as hardware
    import serial
    monkeypatch.setattr(hardware, 'probe_bladerf', lambda: pytest.fail('device queried'))
    monkeypatch.setattr(serial, 'Serial', lambda *a, **k: pytest.fail('port opened'))
    for name, function in [('hardware_review', 'review_experiment'), ('hardware_cases', 'list_cases'),
                           ('hardware_learning', 'training_readiness')]:
        module(monkeypatch, name, **{function: lambda *a, **k: pytest.fail('constructor read files')})
    widget, errors = panel()
    assert not errors and not widget.is_busy()
    assert widget.operator_label.currentText() == 'unknown'
    assert not widget.training_selected.isChecked()
    assert not widget.model_path.text()
    assert not widget.save_case_btn.isEnabled()
    assert not widget.report_btn.isEnabled()
    assert widget.shutdown()


def test_missing_paths_are_rejected_without_starting_worker(app, tmp_path, monkeypatch):
    module(monkeypatch, 'hardware_review', review_experiment=lambda *a, **k: pytest.fail('invalid path read'))
    widget, errors = panel()
    assert widget.review_saved() is False
    widget.experiment_path.setText(str(tmp_path / 'missing'))
    assert widget.review_saved() is False
    widget.experiment_path.setText(str(tmp_path))
    widget.receiver_path.setText(str(tmp_path / 'missing-receiver'))
    assert widget.review_saved() is False
    assert len(errors) == 3 and not widget.is_busy()
    assert widget.check_readiness() is False


def test_review_worker_preserves_original_and_selected_outcomes(app, tmp_path, monkeypatch):
    calls = []
    main_thread = threading.get_ident()
    def review(path, *, receiver_run):
        calls.append((path, receiver_run, threading.get_ident()))
        return reviewed()
    module(monkeypatch, 'hardware_review', review_experiment=review)
    widget, errors = panel()
    activity = []
    widget.activity_changed.connect(activity.append)
    widget.experiment_path.setText(str(tmp_path))
    widget.receiver_path.setText(str(tmp_path))
    assert widget.review_saved()
    assert not widget.review_btn.isEnabled()
    wait(app, widget)
    assert not errors and calls[0] == (tmp_path, tmp_path, calls[0][2])
    assert calls[0][2] != main_thread
    assert activity == [True, False]
    shown = widget.review_output.toPlainText()
    assert 'capture_without_complete_payload' in shown
    assert 'Original known payload complete: No' in shown
    assert 'Selected known payload complete: Yes' in shown
    assert 'New RF transmission during review: No' in shown
    assert '<operator supplied title>' in shown
    assert 'hypothesis' in shown and 'Next measurement:' in shown
    assert widget.report_btn.isEnabled()
    widget.experiment_path.setText(str(tmp_path / 'different'))
    assert widget.review is None and not widget.report_btn.isEnabled()


def test_file_failure_surfaces_error_and_releases_controls(app, tmp_path, monkeypatch):
    def corrupt(*a, **k):
        raise ValueError('Recording hash mismatch')
    module(monkeypatch, 'hardware_review', review_experiment=corrupt)
    widget, errors = panel()
    widget.experiment_path.setText(str(tmp_path))
    assert widget.review_saved()
    wait(app, widget)
    assert errors == ['ValueError: Recording hash mismatch']
    assert widget.review is None and widget.review_btn.isEnabled()


def test_busy_panel_blocks_close_and_retains_worker(app, tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    def slow(*a, **k):
        started.set()
        assert release.wait(3)
        return reviewed()
    module(monkeypatch, 'hardware_review', review_experiment=slow)
    widget, errors = panel()
    widget.experiment_path.setText(str(tmp_path))
    widget.show()
    assert widget.review_saved() and started.wait(1)
    assert widget.task in _ACTIVE_WORKERS
    assert not widget.shutdown()
    assert not widget.close() and widget.isVisible()
    assert 'Wait for the current file operation' in widget.status.text()
    assert widget.review_saved() is False
    assert errors[-1] == 'Wait for the current file operation to finish.'
    release.set()
    wait(app, widget)
    assert widget.shutdown() and widget.close()


def test_deleted_parent_does_not_destroy_running_thread(app, tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    def slow(*a, **k):
        started.set()
        assert release.wait(3)
        return reviewed()
    module(monkeypatch, 'hardware_review', review_experiment=slow)
    parent = QWidget()
    widget = HardwareReviewPanel(parent=parent)
    widget.experiment_path.setText(str(tmp_path))
    assert widget.review_saved() and started.wait(1)
    worker = widget.task
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert worker in _ACTIVE_WORKERS and worker.isRunning()
    release.set()
    deadline = time.monotonic() + 5
    while worker in _ACTIVE_WORKERS and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.003)
    assert worker not in _ACTIVE_WORKERS


def test_case_save_requires_session_and_evidence_note(app, tmp_path, monkeypatch):
    calls = []
    module(monkeypatch, 'hardware_cases',
           add_case=lambda *a, **k: calls.append((a, k)),
           list_cases=lambda p: {'casebook_path': str(p), 'cases': []})
    widget, errors = panel()
    widget.experiment_path.setText(str(tmp_path))
    widget.casebook_path.setText(str(tmp_path))
    widget.review = reviewed()
    assert widget.save_case() is False
    widget.session_id.setText('bench-A')
    widget.operator_label.setCurrentText('receiver_processing')
    assert widget.save_case() is False
    assert not calls and 'evidence note' in errors[-1]
    widget.notes.setPlainText('Same saved IQ recovered after carrier tracking was corrected.')
    assert widget.save_case()
    wait(app, widget)
    arguments = calls[0][1]
    assert arguments['session_id'] == 'bench-A'
    assert arguments['label'] == 'receiver_processing'
    assert arguments['include_for_training'] is False
    assert arguments['receiver_run'] is None


def test_case_load_and_annotation_are_explicit(app, tmp_path, monkeypatch):
    record = {'case_id': 'case-A', 'session_id': 'bench-A',
              'annotation': {'label': 'unknown', 'notes': '', 'include_for_training': False}}
    calls = []
    def annotate(path, case_id, **kwargs):
        calls.append((path, case_id, kwargs))
        record['annotation'] = dict(kwargs)
        return record
    module(monkeypatch, 'hardware_cases',
           list_cases=lambda p: {'cases': [copy.deepcopy(record)]}, annotate_case=annotate)
    widget, errors = panel()
    widget.casebook_path.setText(str(tmp_path))
    assert widget.load_cases()
    wait(app, widget)
    assert not calls and widget.case_list.count() == 2
    widget.case_list.setCurrentIndex(1)
    assert widget.session_id.text() == 'bench-A'
    assert widget.operator_label.currentText() == 'unknown'
    widget.operator_label.setCurrentText('healthy')
    widget.notes.setPlainText('All known payload bytes recovered from the physical recording.')
    widget.training_selected.setChecked(True)
    assert widget.save_annotation()
    wait(app, widget)
    assert not errors and calls[0][1] == 'case-A'
    assert calls[0][2]['include_for_training'] is True


def test_library_creation_and_audit_are_explicit_worker_actions(app, tmp_path, monkeypatch):
    calls = []
    def create(path):
        calls.append(('create', path, threading.get_ident()))
        path.mkdir()
        return {'cases': []}
    def audit(path):
        calls.append(('audit', path, threading.get_ident()))
        return {'status': 'integrity_failed', 'cases': [
            {'case_id': 'damaged', 'session_id': 'bench-A', 'annotation': {'label': 'unknown'},
             'integrity_ok': False, 'problems': ['raw_rx_changed']}]}
    module(monkeypatch, 'hardware_cases', create_casebook=create,
           list_cases=lambda path: {'cases': []}, audit_casebook=audit)
    widget, errors = panel()
    assert not calls
    destination = tmp_path / 'new-library'
    assert widget.create_casebook(destination)
    wait(app, widget)
    assert widget.casebook_path.text() == str(destination)
    assert calls[0][:2] == ('create', destination)
    assert calls[0][2] != threading.get_ident()
    assert widget.audit_cases()
    wait(app, widget)
    assert not errors and calls[1][0] == 'audit'
    assert 'integrity_failed' in widget.status.text()
    assert 'raw_rx_changed' in widget.case_output.toPlainText()


def test_blocked_training_never_claims_a_model_was_created(app, tmp_path, monkeypatch):
    readiness = {'status': 'blocked', 'reasons': ['Only one independent hardware session'],
                 'counts': {'independent_sessions': 1}}
    calls = []
    def train(casebook, output):
        calls.append((casebook, output))
        return readiness
    module(monkeypatch, 'hardware_learning', training_readiness=lambda p: readiness,
           train_hardware_model=train)
    widget, errors = panel()
    widget.casebook_path.setText(str(tmp_path))
    assert widget.check_readiness()
    wait(app, widget)
    assert 'Training blocked' in widget.readiness_status.text()
    assert 'Only one independent hardware session' in widget.learning_output.toPlainText()
    assert not calls
    assert widget.train_model(tmp_path / 'not-created')
    wait(app, widget)
    assert not errors and len(calls) == 1
    assert not widget.model_path.text()
    assert 'did not create' in widget.model_status.text()
    assert 'No hardware model was created' in widget.status.text()


def test_model_and_rule_assessments_do_not_mutate_operator_labels(app, tmp_path, monkeypatch):
    calls = []
    model = tmp_path / 'model.json'
    model.write_text('{}')
    def diagnose(review, selected):
        calls.append(selected)
        return {'label': 'unknown', 'suggested_label': 'low_snr',
                'unknown_reasons': ['Outside training support'], 'limitations': []}
    module(monkeypatch, 'hardware_learning', diagnose_hardware=diagnose,
           train_hardware_model=lambda *a: {'status': 'trained', 'model_path': str(model)})
    widget, errors = panel()
    widget.casebook_path.setText(str(tmp_path))
    widget.review = reviewed()
    widget.operator_label.setCurrentText('healthy')
    widget.notes.setPlainText('Operator evidence remains independent.')
    assert widget.diagnose()
    wait(app, widget)
    assert calls == [None]
    assert 'Evidence rules only' in widget.model_status.text()
    assert widget.operator_label.currentText() == 'healthy'
    assert widget.train_model(tmp_path / 'model-output')
    wait(app, widget)
    assert widget.model_path.text() == str(model)
    assert 'Hardware model created' in widget.model_status.text()
    assert widget.diagnose()
    wait(app, widget)
    assert not errors and calls[-1] == model
    assert widget.operator_label.currentText() == 'healthy'
    assert 'Hardware assessment: unknown' in widget.model_status.text()


def test_report_and_dataset_exports_require_explicit_destinations(app, tmp_path, monkeypatch):
    calls = []
    module(monkeypatch, 'hardware_reports', save_hardware_review=lambda *a, **k:
           calls.append(('report', a, k)) or {'report_path': str(a[1] / 'report.html')})
    module(monkeypatch, 'hardware_cases', export_dataset=lambda *a:
           calls.append(('dataset', a)) or {'status': 'exported', 'records': [], 'excluded': []})
    widget, errors = panel()
    assert widget.save_report(tmp_path / 'missing-review') is False
    widget.review = reviewed()
    widget.casebook_path.setText(str(tmp_path))
    assert widget.save_report(tmp_path / 'report')
    wait(app, widget)
    assert calls[0][1][0] is widget.review
    assert calls[0][2] == {'diagnosis': None}
    assert widget.export_cases(tmp_path / 'dataset.json')
    wait(app, widget)
    assert calls[1][1] == (tmp_path, tmp_path / 'dataset.json')
    assert 'No model was trained' in widget.status.text()
