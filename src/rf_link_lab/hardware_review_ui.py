"""Native saved-hardware evidence and operator-labelled learning workspace.

This panel never opens a device. Every read, annotation, export and model action
requires a button press; potentially expensive file work runs outside the UI.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTabWidget, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget,
)


LABELS = (
    'unknown', 'healthy', 'frequency_offset', 'low_snr', 'clipping',
    'transport_fault', 'receiver_processing', 'other',
)
# Retain threads independently of their panel. A parent window being deleted
# must not destroy a QThread while it is still hashing a recording or training.
_ACTIVE_WORKERS: set['_FileTask'] = set()


class _FileTask(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable):
        super().__init__()
        self.operation = operation

    def run(self):
        try:
            self.completed.emit(self.operation())
        except Exception as exc:
            self.failed.emit(f'{type(exc).__name__}: {exc}')


def _label(value: str, kind: str | None = None) -> QLabel:
    widget = QLabel(value)
    widget.setWordWrap(True)
    if kind:
        widget.setObjectName(kind)
    return widget


def _pretty(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _shown(value) -> str:
    if value is True:
        return 'Yes'
    if value is False:
        return 'No'
    if value is None:
        return 'Not recorded'
    return str(value)


class HardwareReviewPanel(QWidget):
    """Review existing files, curate real cases and explicitly qualify a model."""

    activity_changed = Signal(bool)

    def __init__(self, on_error: Callable[[str], None] | None = None, parent=None):
        super().__init__(parent)
        self.on_error = on_error
        self.task: _FileTask | None = None
        self.review: dict | None = None
        self.diagnosis: dict | None = None
        self.case_records: list[dict] = []
        self.last_readiness: dict | None = None
        self.last_report: dict | None = None
        self._result_callback: Callable | None = None
        self._action_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(_label('Hardware evidence & AI', 'title'))
        layout.addWidget(_label(
            'Review a saved physical experiment, record your diagnosis and build '
            'a case library. These actions use saved files; no radio or serial '
            'connection is opened.', 'muted'))
        self.status = _label('Choose a saved experiment to begin. No files have been read.')
        layout.addWidget(self.status)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_review_tab()
        self._build_cases_tab()
        self._build_learning_tab()
        self._update_controls()

    def _button(self, caption: str, action: Callable, primary=False):
        widget = QPushButton(caption)
        widget.clicked.connect(lambda checked=False: action())
        if primary:
            widget.setObjectName('primary')
        self._action_buttons.append(widget)
        return widget

    def _path_row(self, edit: QLineEdit, action: Callable):
        row = QHBoxLayout()
        row.addWidget(edit, 1)
        row.addWidget(self._button('Choose folder…', action))
        return row

    def _build_review_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        form = QFormLayout()
        self.experiment_path = QLineEdit()
        self.experiment_path.setPlaceholderText('Folder containing experiment.json')
        self.receiver_path = QLineEdit()
        self.receiver_path.setPlaceholderText('Optional: a later receiver result from the same recording')
        form.addRow('Physical experiment', self._path_row(self.experiment_path, self.choose_experiment))
        form.addRow('Reprocessed receiver', self._path_row(self.receiver_path, self.choose_receiver))
        layout.addLayout(form)
        row = QHBoxLayout()
        self.review_btn = self._button('Review saved evidence', self.review_saved, True)
        self.report_btn = self._button('Save review report…', self.save_report)
        row.addWidget(self.review_btn)
        row.addWidget(self.report_btn)
        row.addStretch()
        layout.addLayout(row)
        self.outcome = _label('No experiment reviewed.', 'metric')
        layout.addWidget(self.outcome)
        self.review_output = QTextBrowser()
        self.review_output.setMinimumHeight(270)
        self.review_output.setPlainText(
            'The original experiment outcome and a selected reprocessed result '
            'will be shown separately. Reprocessing an existing recording does '
            'not prove a new RF transmission.')
        layout.addWidget(self.review_output, 1)
        self.experiment_path.textChanged.connect(self._invalidate_review)
        self.receiver_path.textChanged.connect(self._invalidate_review)
        self.tabs.addTab(tab, '1 · Evidence review')

    def _build_cases_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        self.casebook_path = QLineEdit()
        self.casebook_path.setPlaceholderText('Existing or new case library folder')
        row = self._path_row(self.casebook_path, self.choose_casebook)
        self.create_btn = self._button('Create library…', self.create_casebook)
        self.load_btn = self._button('Load cases', self.load_cases)
        row.addWidget(self.create_btn)
        row.addWidget(self.load_btn)
        layout.addLayout(row)
        self.casebook_path.textChanged.connect(self._invalidate_cases)
        form = QFormLayout()
        self.session_id = QLineEdit()
        self.session_id.setMaxLength(128)
        self.session_id.setPlaceholderText('Explicit bench session ID, e.g. bench-20261021-A')
        self.operator_label = QComboBox()
        self.operator_label.addItems(LABELS)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText('What measurement supports this label? Required for a known label.')
        self.notes.setMaximumHeight(90)
        self.training_selected = QCheckBox('Include this case when training criteria are met')
        self.training_selected.setChecked(False)
        form.addRow('Bench session', self.session_id)
        form.addRow('Operator label', self.operator_label)
        form.addRow('Evidence note', self.notes)
        form.addRow('', self.training_selected)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.save_case_btn = self._button('Save reviewed experiment as a case', self.save_case, True)
        self.audit_btn = self._button('Check library integrity', self.audit_cases)
        row.addWidget(self.save_case_btn)
        row.addWidget(self.audit_btn)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.case_list = QComboBox()
        self.case_list.setMinimumContentsLength(24)
        self.case_list.currentIndexChanged.connect(self._select_case)
        row.addWidget(self.case_list, 1)
        self.annotate_btn = self._button('Save label revision', self.save_annotation)
        row.addWidget(self.annotate_btn)
        layout.addLayout(row)
        self.case_output = QTextBrowser()
        self.case_output.setMinimumHeight(100)
        self.case_output.setPlainText('No library loaded. Labels are operator annotations, not AI predictions.')
        layout.addWidget(self.case_output, 1)
        self.tabs.addTab(tab, '2 · Case library')

    def _build_learning_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.addWidget(_label(
            'Train only when the case library has enough independently recorded '
            'and labelled hardware sessions. Unknown cases and repeated copies '
            'do not establish a useful training set.', 'muted'))
        row = QHBoxLayout()
        self.readiness_btn = self._button('Check training readiness', self.check_readiness, True)
        self.export_btn = self._button('Export eligible dataset…', self.export_cases)
        self.train_btn = self._button('Train hardware model…', self.train_model)
        for button in (self.readiness_btn, self.export_btn, self.train_btn):
            row.addWidget(button)
        layout.addLayout(row)
        self.readiness_status = _label('Training readiness has not been checked.', 'metric')
        layout.addWidget(self.readiness_status)
        self.learning_output = QTextBrowser()
        self.learning_output.setPlainText(
            'Evidence review uses explicit rules and identifies hypotheses. '
            'A learned hardware model is separate and is not created until '
            'the library passes its qualification checks.')
        layout.addWidget(self.learning_output, 1)
        row = QHBoxLayout()
        self.model_path = QLineEdit()
        self.model_path.setPlaceholderText('Qualified hardware model file')
        row.addWidget(self.model_path, 1)
        row.addWidget(self._button('Choose model…', self.choose_model))
        self.diagnose_btn = self._button('Assess reviewed recording', self.diagnose)
        row.addWidget(self.diagnose_btn)
        layout.addLayout(row)
        self.model_status = _label('No hardware model loaded or trained.')
        layout.addWidget(self.model_status)
        self.diagnosis_output = QTextBrowser()
        self.diagnosis_output.setMaximumHeight(190)
        self.diagnosis_output.setPlainText('An assessment is a suggestion. It never changes the operator label.')
        layout.addWidget(self.diagnosis_output)
        self.tabs.addTab(tab, '3 · Hardware learning')

    def _error(self, message: str):
        self.status.setText(message)
        if self.on_error:
            self.on_error(message)

    def is_busy(self) -> bool:
        # A finished signal may still be queued; do not replace its callback.
        return self.task is not None

    def shutdown(self, wait_ms=0) -> bool:
        """Return False while a file job is active; never terminate its thread."""
        if self.task is not None and wait_ms:
            self.task.wait(max(0, int(wait_ms)))
        return self.task is None or not self.task.isRunning()

    def closeEvent(self, event):
        if self.is_busy():
            self.status.setText('Wait for the current file operation to finish before closing this panel.')
            event.ignore()
        else:
            super().closeEvent(event)

    def _start_task(self, description: str, operation: Callable, completed: Callable):
        if self.is_busy():
            self._error('Wait for the current file operation to finish.')
            return False
        worker = _FileTask(operation)
        self.task = worker
        self._result_callback = completed
        _ACTIVE_WORKERS.add(worker)
        worker.completed.connect(self._task_result)
        worker.failed.connect(self._task_error)
        worker.finished.connect(self._task_finished)
        worker.finished.connect(lambda w=worker: _ACTIVE_WORKERS.discard(w))
        worker.finished.connect(worker.deleteLater)
        self.status.setText(description)
        self._update_controls()
        self.activity_changed.emit(True)
        worker.start()
        return True

    @Slot(object)
    def _task_result(self, result):
        if self._result_callback:
            try:
                self._result_callback(result)
            except Exception as exc:
                self._error(f'Result could not be displayed: {exc}')

    @Slot(str)
    def _task_error(self, message):
        self._error(message)

    @Slot()
    def _task_finished(self):
        self.task = None
        self._result_callback = None
        self._update_controls()
        self.activity_changed.emit(False)

    def _update_controls(self):
        busy = self.is_busy()
        for button in self._action_buttons:
            button.setEnabled(not busy)
        for edit in (self.experiment_path, self.receiver_path, self.casebook_path,
                     self.session_id, self.operator_label, self.notes,
                     self.training_selected, self.case_list, self.model_path):
            edit.setEnabled(not busy)
        if busy:
            return
        has_review = self.review is not None
        has_library = bool(self.casebook_path.text().strip())
        self.report_btn.setEnabled(has_review)
        self.save_case_btn.setEnabled(has_review and has_library)
        self.annotate_btn.setEnabled(bool(self.case_list.currentData()))
        for button in (self.load_btn, self.audit_btn, self.readiness_btn,
                       self.export_btn, self.train_btn):
            button.setEnabled(has_library)
        self.diagnose_btn.setEnabled(has_review)

    def _invalidate_review(self):
        self.review = None
        self.diagnosis = None
        self.outcome.setText('Selection changed. Review the saved evidence again.')
        self._update_controls()

    def _invalidate_cases(self):
        self.case_records = []
        self.case_list.clear()
        self.last_readiness = None
        self.readiness_status.setText('Training readiness has not been checked for this library.')
        self._update_controls()

    def _existing(self, edit: QLineEdit, description: str) -> Path | None:
        value = edit.text().strip()
        if not value:
            self._error(f'Choose {description} first.')
            return None
        path = Path(value).expanduser()
        if not path.exists():
            self._error(f'{description.capitalize()} does not exist: {path}')
            return None
        return path

    def _choose_dir(self, edit: QLineEdit, title: str):
        folder = QFileDialog.getExistingDirectory(self, title, edit.text())
        if folder:
            edit.setText(folder)

    def choose_experiment(self):
        self._choose_dir(self.experiment_path, 'Choose saved physical experiment')

    def choose_receiver(self):
        self._choose_dir(self.receiver_path, 'Choose reprocessed receiver result')

    def choose_casebook(self):
        self._choose_dir(self.casebook_path, 'Choose case library')

    def choose_model(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Choose hardware model', self.model_path.text(), 'JSON model (*.json)')
        if filename:
            self.model_path.setText(filename)
            self.model_status.setText('Model selected. Assessment will verify its contents.')

    def review_saved(self):
        experiment = self._existing(self.experiment_path, 'saved experiment')
        if experiment is None:
            return False
        receiver = None
        if self.receiver_path.text().strip():
            receiver = self._existing(self.receiver_path, 'reprocessed receiver result')
            if receiver is None:
                return False
        def operation():
            from .hardware_review import review_experiment
            return review_experiment(experiment, receiver_run=receiver)
        self.review = None
        self.diagnosis = None
        return self._start_task('Checking saved recording, receiver result and source hashes…', operation, self._review_complete)

    def _review_complete(self, result):
        self.review = result
        outcome = result.get('outcome', {})
        complete = outcome.get('selected_known_payload_complete')
        self.outcome.setText('Selected receiver: ' + (
            'all known payloads recovered' if complete is True else
            'known payload incomplete' if complete is False else 'payload result unavailable'))
        rows = [
            ('Original recorded experiment', outcome.get('experiment_status')),
            ('Original known payload complete', outcome.get('recorded_known_payload_complete')),
            ('Selected receiver status', outcome.get('receiver_status')),
            ('Selected known payload complete', complete),
            ('Original physical TX requested', outcome.get('physical_tx_requested')),
            ('New RF transmission during review', outcome.get('new_rf_transmission')),
        ]
        fragments = ['<h3>Recorded outcome and selected receiver</h3>']
        fragments.extend('<p><b>' + html.escape(k) + ':</b> ' + html.escape(_shown(v)) + '</p>' for k, v in rows)
        fragments.append('<h3>Evidence rules and hypotheses</h3>')
        for finding in result.get('findings', []):
            title = html.escape(str(finding.get('title', finding.get('code', 'Finding'))))
            kind = html.escape(str(finding.get('kind', 'observation')))
            severity = html.escape(str(finding.get('severity', 'info')))
            fragments.append(f'<p><b>{title}</b> · {kind} · {severity}</p>')
            fragments.append('<pre>' + html.escape(_pretty(finding.get('evidence', {}))) + '</pre>')
            if finding.get('next_measurement'):
                fragments.append('<p><b>Next measurement:</b> ' + html.escape(str(finding['next_measurement'])) + '</p>')
        if not result.get('findings'):
            fragments.append('<p>No rule findings were returned.</p>')
        fragments.append('<h3>Six blind hardware inputs</h3><pre>' + html.escape(_pretty(result.get('blind_features', {}))) + '</pre>')
        fragments.append('<p>The clipping input counts exact raw ADC endpoints across the entire recording. '
                         'Historical receiver peak proxies remain separate.</p>')
        fragments.append('<h3>Raw ADC endpoint check</h3><pre>' + html.escape(_pretty(result.get('raw_adc_rail_measurement', {}))) + '</pre>')
        fragments.append('<h3>Recorded receiver values (comparison evidence)</h3><pre>' + html.escape(_pretty(result.get('receiver_metrics', {}))) + '</pre>')
        fragments.append('<h3>Scope and limitations</h3>')
        fragments.extend('<p>' + html.escape(str(value)) + '</p>' for value in result.get('limitations', []))
        fragments.append('<h3>Source identity</h3><pre>' + html.escape(_pretty(result.get('source', {}))) + '</pre>')
        self.review_output.setHtml(''.join(fragments))
        self.status.setText('Saved evidence reviewed. No new RF transmission was made.')

    def save_report(self, path=None):
        if self.review is None:
            self._error('Review a saved experiment before saving its report.')
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, 'Choose a new review report folder', '', 'New folder (*)')
        if not path:
            return False
        review, diagnosis = self.review, self.diagnosis
        def operation():
            from .hardware_reports import save_hardware_review
            return save_hardware_review(review, Path(path), diagnosis=diagnosis)
        def completed(result):
            self.last_report = result
            self.status.setText('Review report saved: ' + str(result.get('report', result.get('report_path', path))))
        return self._start_task('Saving review report and evidence identity…', operation, completed)

    def create_casebook(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, 'Choose a new case library folder', self.casebook_path.text(), 'New folder (*)')
        if not path:
            return False
        selected = Path(path).expanduser()
        def operation():
            from .hardware_cases import create_casebook, list_cases
            create_casebook(selected)
            return list_cases(selected)
        self.casebook_path.setText(str(selected))
        return self._start_task('Creating a case library…', operation, self._cases_complete)

    def load_cases(self):
        path = self._existing(self.casebook_path, 'case library')
        if path is None:
            return False
        def operation():
            from .hardware_cases import list_cases
            return list_cases(path)
        return self._start_task('Reading case annotations and verifying saved reviews…', operation, self._cases_complete)

    def _cases_complete(self, result):
        self.case_records = list(result.get('cases', []))
        self.case_list.blockSignals(True)
        self.case_list.clear()
        self.case_list.addItem('Choose a saved case to revise its label', None)
        for case in self.case_records:
            annotation = case.get('annotation', {})
            self.case_list.addItem(
                f"{case.get('session_id', '?')} · {annotation.get('label', 'unknown')} · {case.get('case_id', '?')}",
                case.get('case_id'))
        self.case_list.blockSignals(False)
        self.last_readiness = None
        self.readiness_status.setText('Library changed. Check training readiness again.')
        self.case_output.setPlainText(f"{len(self.case_records)} saved cases. Labels are operator annotations.\n\n" + _pretty(result))
        self.status.setText('Case library loaded; no labels were inferred.')

    def _annotation(self):
        label = self.operator_label.currentText()
        notes = self.notes.toPlainText().strip()
        if label != 'unknown' and not notes:
            self._error('Add a measurement or evidence note before assigning a known label.')
            return None
        return {'label': label, 'notes': notes, 'include_for_training': self.training_selected.isChecked()}

    def save_case(self):
        if self.review is None:
            self._error('Review the saved experiment before adding a case.')
            return False
        path = self._existing(self.casebook_path, 'case library')
        if path is None:
            return False
        session = self.session_id.text().strip()
        if not session:
            self._error('Enter the bench session ID for this physical recording.')
            return False
        annotation = self._annotation()
        if annotation is None:
            return False
        experiment = Path(self.experiment_path.text().strip()).expanduser()
        receiver = Path(self.receiver_path.text().strip()).expanduser() if self.receiver_path.text().strip() else None
        def operation():
            from .hardware_cases import add_case, list_cases
            add_case(path, experiment, session_id=session, receiver_run=receiver, **annotation)
            return list_cases(path)
        return self._start_task('Saving reviewed evidence with your explicit annotation…', operation, self._cases_complete)

    def _select_case(self):
        selected = self.case_list.currentData()
        case = next((case for case in self.case_records if case.get('case_id') == selected), None)
        if case:
            annotation = case.get('annotation', {})
            self.session_id.setText(str(case.get('session_id', '')))
            label = annotation.get('label', 'unknown')
            self.operator_label.setCurrentText(label if label in LABELS else 'unknown')
            self.notes.setPlainText(str(annotation.get('notes', '')))
            self.training_selected.setChecked(bool(annotation.get('include_for_training', False)))
            self.case_output.setPlainText(_pretty(case))
        self._update_controls()

    def save_annotation(self):
        path = self._existing(self.casebook_path, 'case library')
        if path is None:
            return False
        selected = self.case_list.currentData()
        if not selected:
            self._error('Choose a saved case before revising its label.')
            return False
        annotation = self._annotation()
        if annotation is None:
            return False
        def operation():
            from .hardware_cases import annotate_case, list_cases
            annotate_case(path, selected, **annotation)
            return list_cases(path)
        return self._start_task('Appending an operator label revision…', operation, self._cases_complete)

    def audit_cases(self):
        path = self._existing(self.casebook_path, 'case library')
        if path is None:
            return False
        def operation():
            from .hardware_cases import audit_casebook
            return audit_casebook(path)
        def completed(result):
            self._cases_complete(result)
            self.status.setText('Library integrity check: ' + str(result.get('status', 'complete')))
        return self._start_task('Checking case integrity, capture identity and eligibility…', operation, completed)

    def check_readiness(self):
        path = self._existing(self.casebook_path, 'case library')
        if path is None:
            return False
        def operation():
            from .hardware_learning import training_readiness
            return training_readiness(path)
        return self._start_task('Checking independent captures, labels and session splits…', operation, self._readiness_complete)

    def _readiness_complete(self, result):
        self.last_readiness = result
        ready = result.get('status') == 'ready'
        self.readiness_status.setText('Library meets training criteria.' if ready else 'Training blocked: more qualified hardware evidence is needed.')
        reasons = result.get('reasons', [])
        self.learning_output.setPlainText(('\n'.join(str(reason) for reason in reasons) + '\n\n' if reasons else '') + _pretty(result))
        self.status.setText('Training readiness checked. No model was trained.')

    def export_cases(self, path=None):
        casebook = self._existing(self.casebook_path, 'case library')
        if casebook is None:
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, 'Choose a new dataset file', '', 'JSON dataset (*.json)')
        if not path:
            return False
        def operation():
            from .hardware_cases import export_dataset
            return export_dataset(casebook, Path(path))
        def completed(result):
            self.learning_output.setPlainText(_pretty(result))
            self.status.setText('Eligible cases exported with exclusion reasons. No model was trained.')
        return self._start_task('Verifying cases and exporting eligible measured features…', operation, completed)

    def train_model(self, path=None):
        casebook = self._existing(self.casebook_path, 'case library')
        if casebook is None:
            return False
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, 'Choose a new hardware model output folder', '', 'New folder (*)')
        if not path:
            return False
        def operation():
            from .hardware_learning import train_hardware_model
            return train_hardware_model(casebook, Path(path))
        def completed(result):
            self.learning_output.setPlainText(_pretty(result))
            if result.get('status') == 'trained' and result.get('model_path'):
                self.model_path.setText(str(result['model_path']))
                self.model_status.setText('Hardware model created. Review held-out session results before relying on suggestions.')
                self.readiness_status.setText('Training complete; evaluation is recorded separately.')
                self.status.setText('A hardware model was created from qualified case evidence.')
            else:
                self.model_status.setText('This training request did not create a hardware model.')
                self._readiness_complete(result.get('readiness', result))
                self.status.setText('Training was blocked. No hardware model was created by this request.')
        return self._start_task('Checking qualification and, if eligible, training on measured hardware cases…', operation, completed)

    def diagnose(self):
        if self.review is None:
            self._error('Review a saved experiment before requesting an assessment.')
            return False
        model = None
        if self.model_path.text().strip():
            model = self._existing(self.model_path, 'hardware model')
            if model is None:
                return False
        review = self.review
        def operation():
            from .hardware_learning import diagnose_hardware
            return diagnose_hardware(review, model)
        def completed(result):
            self.diagnosis = result
            self.diagnosis_output.setPlainText(_pretty(result))
            self.model_status.setText(
                'Hardware assessment: ' + str(result.get('label', 'unknown')) +
                '. Operator annotations remain unchanged.' if model else
                'Evidence rules only. No learned hardware model was supplied.')
            self.status.setText('Assessment saved in this review. The operator label was not changed.')
        return self._start_task('Assessing saved measurements and checking model support…', operation, completed)
