"""Native finite-link preparation and explicit experiment controls.

Constructing this panel opens no device. All worker inputs are copied on the
GUI thread; workers never read widgets. Rehearsal remains a labelled software
channel and requires neither a connected board nor a connection plan.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
    QTextBrowser, QVBoxLayout, QWidget,
)

from .paths import state_root
from .protocol import Telemetry


def _label(message, kind=None):
    widget = QLabel(message)
    widget.setWordWrap(True)
    if kind:
        widget.setObjectName(kind)
    return widget


class _ExperimentTask(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.completed.emit(self.function())
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ConductedPanel(QWidget):
    activity_changed = Signal(bool)
    experiment_completed = Signal(object)

    def __init__(self, get_serial_snapshot, on_result, on_error):
        super().__init__()
        self.get_serial_snapshot = get_serial_snapshot
        self.on_result = on_result
        self.on_error = on_error
        self.task = None
        self.cancel_event = None
        self._busy = False
        self._mode = None
        self._prepared_fingerprint = None
        self._validated_fingerprint = None
        self._loaded_plan = None
        self._live_review_required = False
        self.tx_reference_path = None
        self.payload_source = None
        self.last_result = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        layout.addWidget(_label('FINITE CONDUCTED LINK', 'eyebrow'))
        layout.addWidget(_label(
            'Prepare a finite payload, rehearse without a board, then use explicit physical TX only after the connection and power plan passes.',
            'muted',
        ))
        actions = QHBoxLayout()
        self.prepare_btn = QPushButton('Prepare finite TX files')
        self.prepare_btn.clicked.connect(lambda: self.prepare_waveform())
        actions.addWidget(self.prepare_btn)
        self.rehearsal_btn = QPushButton('Run software rehearsal')
        self.rehearsal_btn.clicked.connect(lambda: self.start_rehearsal())
        actions.addWidget(self.rehearsal_btn)
        self.live_btn = QPushButton('Start physical TX + RX once')
        self.live_btn.setObjectName('primary')
        self.live_btn.clicked.connect(lambda: self.start_live())
        actions.addWidget(self.live_btn)
        self.stop_btn = QPushButton('Stop finite run')
        self.stop_btn.clicked.connect(self.stop)
        actions.addWidget(self.stop_btn)
        layout.addLayout(actions)
        self.status = _label('No waveform prepared. No serial or RF device has been opened.', 'muted')
        layout.addWidget(self.status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        columns = QGridLayout(body)
        columns.setColumnStretch(0, 1)
        columns.setColumnStretch(1, 1)
        left = QVBoxLayout()
        right = QVBoxLayout()
        columns.addLayout(left, 0, 0)
        columns.addLayout(right, 0, 1)
        left.addWidget(_label('PAYLOAD AND DEVICE SETTINGS', 'eyebrow'))
        form = QFormLayout()
        self.source = QComboBox()
        self.source.addItem('Offline example telemetry | no Nucleo evidence', 'offline_example')
        self.source.addItem('Current physical Nucleo serial packets', 'current_serial')
        form.addRow('Payload source', self.source)
        self.serial = QLineEdit()
        self.serial.setMaxLength(32)
        self.serial.setPlaceholderText('Exact 32-digit bladeRF serial; required only for physical TX')
        selected_serial = os.environ.get('RF_LINK_LAB_SELECTED_SERIAL', '')
        if re.fullmatch(r'[0-9a-fA-F]{32}', selected_serial):
            self.serial.setText(selected_serial.lower())
            self.serial.setReadOnly(True)
            self.serial.setToolTip('Selected by the hardware launcher; start a new hardware session to choose another radio.')
        form.addRow('Selected serial', self.serial)
        self.frequency = self._number(915_000_000, 70_000_000, 6_000_000_000)
        form.addRow('Center frequency (Hz)', self.frequency)
        gains = QHBoxLayout()
        self.rx_gain = self._integer(0, -5, 50)
        self.tx_gain = self._integer(-20, -23, 66)
        gains.addWidget(_label('RX dB')); gains.addWidget(self.rx_gain)
        gains.addWidget(_label('TX dB')); gains.addWidget(self.tx_gain)
        form.addRow('Gain controls', gains)
        self.sample_rate = self._integer(1_000_000, 1_000_000, 5_000_000)
        form.addRow('Sample rate (samples/s)', self.sample_rate)
        self.symbol_rate = self._integer(50_000, 10_000, 250_000)
        form.addRow('QPSK symbol rate (symbols/s)', self.symbol_rate)
        self.tx_scale = self._number(.25, .01, .90, decimals=3)
        form.addRow('Digital I/Q component peak', self.tx_scale)
        self.rx_samples = self._integer(3_000_000, 1_000_000, 5_000_000)
        form.addRow('Finite RX sample budget', self.rx_samples)
        self.bandwidth = self._integer(200_000, 200_000, 5_000_000)
        form.addRow('Requested bandwidth (Hz)', self.bandwidth)
        left.addLayout(form)
        left.addWidget(_label(
            'One burst, at most 12 selected packets, never repeated. Digital peak and TX gain do not establish output power in dBm. Preparation and rehearsal preserve their source labels.',
            'muted',
        ))
        left.addStretch()

        right.addWidget(_label('OPERATOR CONNECTION AND POWER PLAN', 'eyebrow'))
        right.addWidget(_label(
            'Attenuator specifications and TX power bound are currently unknown. Blank fields block physical TX. Software rehearsal needs no plan.',
            'muted',
        ))
        plan_form = QFormLayout()
        self.attenuator_label = QLineEdit()
        self.attenuator_label.setPlaceholderText('Identify the actual component')
        self.attenuation = QLineEdit(); self.attenuation.setPlaceholderText('Known conservative minimum loss, dB')
        self.frequency_min = QLineEdit(); self.frequency_min.setPlaceholderText('Component minimum frequency, Hz')
        self.frequency_max = QLineEdit(); self.frequency_max.setPlaceholderText('Component maximum frequency, Hz')
        self.power_rating = QLineEdit(); self.power_rating.setPlaceholderText('Applicable component power rating, W')
        self.cable_loss = self._number(0, 0, 300, decimals=3)
        self.tx_power_bound = QLineEdit(); self.tx_power_bound.setPlaceholderText('Independently established conservative upper bound, dBm')
        self.power_source = QLineEdit(); self.power_source.setMaxLength(2000)
        self.power_source.setPlaceholderText('Source or measurement basis covering these settings and waveform')
        for name, widget in [
            ('Attenuator label', self.attenuator_label), ('Minimum attenuation (dB)', self.attenuation),
            ('Coverage minimum (Hz)', self.frequency_min), ('Coverage maximum (Hz)', self.frequency_max),
            ('Power rating (W)', self.power_rating), ('Minimum cable loss (dB)', self.cable_loss),
            ('TX power upper bound (dBm)', self.tx_power_bound), ('Power-bound basis', self.power_source),
        ]:
            plan_form.addRow(name, widget)
        self._plan_widgets = [self.attenuator_label, self.attenuation, self.frequency_min,
                              self.frequency_max, self.power_rating, self.cable_loss,
                              self.tx_power_bound, self.power_source]
        right.addLayout(plan_form)
        self.connection_checked = QCheckBox('I checked TX1 -> attenuator chain -> RX1 connections.')
        self.unused_loaded = QCheckBox('I checked that unused RF ports are correctly loaded.')
        self.active_antenna = QCheckBox('An active antenna is connected (blocks this conducted workflow).')
        for widget in [self.connection_checked, self.unused_loaded, self.active_antenna]:
            right.addWidget(widget)
            self._plan_widgets.append(widget)
        right.addWidget(_label(
            'Project policy: RX upper bound <= -20 dBm; each attenuator has >=3 dB input-power margin. Passing records operator assertions, not measured power or a hardware rating.',
            'muted',
        ))
        plan_actions = QHBoxLayout()
        self.plan_btn = QPushButton('Check current plan')
        self.plan_btn.clicked.connect(lambda: self.validate_plan())
        plan_actions.addWidget(self.plan_btn)
        self.load_plan_btn = QPushButton('Load plan JSON')
        self.load_plan_btn.clicked.connect(lambda: self.load_plan())
        plan_actions.addWidget(self.load_plan_btn)
        self.form_plan_btn = QPushButton('Use single-stage form')
        self.form_plan_btn.clicked.connect(self.use_form_plan)
        plan_actions.addWidget(self.form_plan_btn)
        right.addLayout(plan_actions)
        self.plan_status = _label('Plan incomplete. Physical TX is disabled.', 'muted')
        right.addWidget(self.plan_status)
        right.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        self.review_checked = QCheckBox('I reviewed the physical connections and device shutdown state after the unconfirmed stop.')
        self.review_checked.setVisible(False)
        layout.addWidget(self.review_checked)
        self.review_btn = QPushButton('Record operator review and require a fresh plan check')
        self.review_btn.setVisible(False)
        self.review_btn.clicked.connect(self.record_operator_review)
        layout.addWidget(self.review_btn)
        self.output = QTextBrowser()
        self.output.setMinimumHeight(140)
        self.output.setMaximumHeight(230)
        self.output.setPlainText('No experiment performed. Preparation, software rehearsal and physical TX are separate actions.')
        layout.addWidget(self.output)
        results = QHBoxLayout()
        self.report_btn = QPushButton('Open last bench report')
        self.report_btn.clicked.connect(self.open_report)
        results.addWidget(self.report_btn)
        self.decode_btn = QPushButton('Show saved decoded I/Q')
        self.decode_btn.clicked.connect(self.show_decoded)
        results.addWidget(self.decode_btn)
        layout.addLayout(results)

        self._editable = [self.source, self.serial, self.frequency, self.rx_gain, self.tx_gain,
                          self.sample_rate, self.symbol_rate, self.tx_scale, self.rx_samples,
                          self.bandwidth, *self._plan_widgets, self.load_plan_btn,
                          self.form_plan_btn, self.plan_btn]
        for widget in [self.serial, self.frequency, self.rx_gain, self.tx_gain, self.rx_samples,
                       self.bandwidth, *self._plan_widgets]:
            self._connect_change(widget, self._invalidate_plan)
        for widget in [self.source, self.sample_rate, self.symbol_rate, self.tx_scale]:
            self._connect_change(widget, self._invalidate_preparation)
        self._update_buttons()

    @staticmethod
    def _number(value, minimum, maximum, decimals=0):
        widget = QDoubleSpinBox()
        widget.setDecimals(decimals); widget.setRange(minimum, maximum); widget.setValue(value)
        return widget

    @staticmethod
    def _integer(value, minimum, maximum):
        widget = QSpinBox()
        widget.setRange(minimum, maximum); widget.setValue(value)
        return widget

    @staticmethod
    def _connect_change(widget, callback):
        if isinstance(widget, QLineEdit): widget.textChanged.connect(callback)
        elif isinstance(widget, QComboBox): widget.currentIndexChanged.connect(callback)
        elif isinstance(widget, QCheckBox): widget.toggled.connect(callback)
        else: widget.valueChanged.connect(callback)

    def is_busy(self):
        return self._busy

    def shutdown(self):
        if self.is_busy():
            self.stop()
            return False
        return True

    def _invalidate_plan(self, *args):
        self._validated_fingerprint = None
        self.plan_status.setText('Plan changed or incomplete. Check it again before physical TX.')
        self._update_buttons()

    def _invalidate_preparation(self, *args):
        self._prepared_fingerprint = None
        self._invalidate_plan()
        self.status.setText('Payload settings changed. Prepare a new finite waveform before running.')

    def _preparation_settings(self):
        return (self.source.currentData(), self.sample_rate.value(), self.symbol_rate.value(), self.tx_scale.value())

    def _prepared_is_current(self):
        return self.tx_reference_path is not None and self._prepared_fingerprint == self._preparation_settings()

    def _update_buttons(self):
        current = self._prepared_is_current()
        self.prepare_btn.setEnabled(not self._busy)
        self.rehearsal_btn.setEnabled(not self._busy and current)
        self.live_btn.setEnabled(not self._busy and current and self._validated_fingerprint is not None and not self._live_review_required)
        self.stop_btn.setEnabled(self._busy and self.cancel_event is not None and not self.cancel_event.is_set())
        self.report_btn.setEnabled(not self._busy and bool(self.last_result and self.last_result.get('bench_manifest')))
        self.decode_btn.setEnabled(not self._busy and bool(self.last_result and self.last_result.get('receiver_manifest')))

    def _choose_new_folder(self, title, prefix):
        folder = state_root() / 'artifacts' / 'conducted'
        folder.mkdir(parents=True, exist_ok=True)
        suggested = folder / f'{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'
        path, _ = QFileDialog.getSaveFileName(self, title, str(suggested), 'New folder (*)')
        return Path(path) if path else None

    def _payload(self):
        if self.source.currentData() == 'offline_example':
            from .conducted import example_payload
            messages = example_payload()
            return messages, {'source': 'offline_example', 'origin_note': 'Software example telemetry; no Nucleo or serial-port evidence.'}
        snapshot = self.get_serial_snapshot()
        if not isinstance(snapshot, dict) or snapshot.get('input_source') != 'serial' or not snapshot.get('session_id'):
            raise ValueError('Select current physical serial input with CRC-valid packets; offline parser bytes are not Nucleo evidence.')
        rows = [row for row in snapshot.get('recent', [])
                if row.get('source') == 'serial' and row.get('session_id') == snapshot['session_id']][-12:]
        if not rows:
            raise ValueError('No CRC-valid physical serial packets exist in the current session.')
        selected = json.loads(json.dumps(rows, allow_nan=False))
        messages = []
        for row in selected:
            if type(row.get('sequence')) is not int or type(row.get('uptime_ms')) is not int or type(row.get('button')) is not bool:
                raise ValueError('Current serial packet values do not match the telemetry schema.')
            messages.append(Telemetry(row['sequence'], row['uptime_ms'], row['button']))
        return messages, {'source': 'current_serial', 'session_id': snapshot['session_id'],
                          'port': snapshot.get('port'), 'selected_rows': selected,
                          'origin_note': 'Host-received current serial packets; board identity is not authenticated.'}

    def prepare_waveform(self, path=None):
        if self.is_busy():
            self.on_error('Wait for the current finite experiment to finish.'); return None
        try:
            messages, source = self._payload()
            path = Path(path) if path is not None else self._choose_new_folder('Choose a new prepared-waveform folder', 'prepared')
            if path is None: return None
            from .conducted_waveform import prepare_tx_waveform
            reference = prepare_tx_waveform(messages, path, sample_rate=self.sample_rate.value(),
                                            symbol_rate=self.symbol_rate.value(), tx_scale=self.tx_scale.value())
            manifest = json.loads(reference.read_text(encoding='utf-8'))
            if manifest['duration_s'] > .5:
                raise ValueError('The live controller accepts a burst lasting at most 0.5 seconds; select fewer frames or a higher symbol rate.')
            with (reference.parent / 'payload-source.json').open('x', encoding='utf-8') as handle:
                handle.write(json.dumps(source, indent=2, allow_nan=False) + '\n')
            self.tx_reference_path = reference
            self.payload_source = source
            self._prepared_fingerprint = self._preparation_settings()
            self._invalidate_plan()
            self.status.setText(f"Prepared {manifest['frames_expected']} frames | {manifest['duration_s']:.4f} s | source {source['source']} | NO RF TRANSMISSION")
            self.output.setPlainText(json.dumps({'prepared_tx_manifest': str(reference), 'payload_source': source,
                                                'sample_rate': manifest['sample_rate'], 'symbol_rate': manifest['symbol_rate'],
                                                'digital_peak': manifest['quantization']['tx_scale'], 'rf_transmitted': False}, indent=2))
            self._update_buttons()
            return reference
        except Exception as exc:
            self._prepared_fingerprint = None
            self._invalidate_plan()
            self.on_error(str(exc))
            return None

    def _settings(self, *, require_serial=False):
        from .bladerf_duplex import DuplexSettings
        serial = self.serial.text().strip()
        if require_serial and not re.fullmatch(r'[0-9a-fA-F]{32}', serial):
            raise ValueError('Select the exact 32-hex-digit bladeRF serial before checking a physical TX plan.')
        settings = DuplexSettings(device_serial=serial, frequency_hz=int(self.frequency.value()),
                                  sample_rate=self.sample_rate.value(), rx_samples=self.rx_samples.value(),
                                  rx_gain_db=self.rx_gain.value(), tx_gain_db=self.tx_gain.value(),
                                  bandwidth_hz=self.bandwidth.value())
        if settings.bandwidth_hz > settings.sample_rate:
            raise ValueError('Requested bandwidth must not exceed sample rate.')
        if not 1 <= settings.rx_samples / settings.sample_rate <= 5:
            raise ValueError('Select an RX sample budget lasting 1 to 5 seconds.')
        return settings

    def _connection_plan(self):
        if self._loaded_plan is not None:
            return json.loads(json.dumps(self._loaded_plan, allow_nan=False))
        try:
            attenuation = float(self.attenuation.text())
            minimum = int(self.frequency_min.text())
            maximum = int(self.frequency_max.text())
            rating = float(self.power_rating.text())
            bound = float(self.tx_power_bound.text())
        except ValueError as exc:
            raise ValueError('Enter known attenuation, frequency coverage, power rating and an independent TX power bound; unknown specifications cannot permit physical TX.') from exc
        return {
            'schema': 'rf-link-lab.conducted-plan.v1', 'device_serial': self.serial.text().strip(),
            'connection': 'TX1_ATTENUATORS_RX1',
            'operator_checked_connections': self.connection_checked.isChecked(),
            'unused_ports_correctly_loaded': self.unused_loaded.isChecked(),
            'active_antenna_connected': self.active_antenna.isChecked(),
            'cable_loss_db': self.cable_loss.value(),
            'attenuators': [{'label': self.attenuator_label.text().strip(), 'attenuation_db': attenuation,
                             'frequency_min_hz': minimum, 'frequency_max_hz': maximum, 'power_rating_w': rating}],
            'tx_power_bound': {'upper_dbm': bound, 'frequency_hz': int(self.frequency.value()),
                               'tx_gain_db': self.tx_gain.value(), 'source_note': self.power_source.text().strip()},
            'rx_working_ceiling_dbm': -20.0,
        }

    def _plan_fingerprint(self, settings, plan):
        return json.dumps({'settings': asdict(settings), 'plan': plan, 'tx_reference_path': str(self.tx_reference_path)},
                          sort_keys=True, allow_nan=False)

    def validate_plan(self):
        if self.is_busy():
            self.on_error('Wait for the current finite experiment before changing its plan.'); return None
        self._validated_fingerprint = None
        try:
            if self._live_review_required:
                raise ValueError('Shutdown was not confirmed. Review the physical device state and record operator review before a new plan check.')
            from .conducted_plan import validate_connection_plan
            settings = self._settings(require_serial=True)
            plan = self._connection_plan()
            result = validate_connection_plan(plan, frequency_hz=settings.frequency_hz,
                                              tx_gain_db=settings.tx_gain_db, device_serial=settings.device_serial)
            self._validated_fingerprint = self._plan_fingerprint(settings, plan)
            budget = result['calculations']
            self.plan_status.setText(f"Operator declarations and project policy passed. RX upper bound {budget['rx_input_upper_bound_dbm']:g} dBm; headroom {budget['rx_headroom_db']:g} dB. No physical connection or power was independently verified.")
            self.output.setPlainText(json.dumps(result, indent=2))
            self._update_buttons()
            return result
        except Exception as exc:
            self.plan_status.setText('Plan incomplete or rejected. Physical TX is disabled. ' + str(exc))
            self._update_buttons()
            self.on_error(str(exc))
            return None

    def load_plan(self, path=None):
        if self.is_busy(): return None
        if path is None:
            filename, _ = QFileDialog.getOpenFileName(self, 'Load reviewed connection plan', str(state_root()), 'JSON plan (*.json)')
            if not filename: return None
            path = filename
        try:
            path = Path(path)
            if path.stat().st_size > 100_000: raise ValueError('Plan JSON exceeds the 100 kB limit.')
            plan = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(plan, dict): raise ValueError('Connection plan JSON must be an object.')
            self._loaded_plan = plan
            for widget in self._plan_widgets: widget.setEnabled(False)
            self._invalidate_plan()
            self.plan_status.setText(f'Loaded declarations from {path.name}. Single-stage form is inactive; selected device/frequency/gain must match the file. Check the plan before physical TX.')
            self.output.setPlainText(json.dumps(plan, indent=2))
            return plan
        except Exception as exc:
            self.on_error(str(exc)); return None

    def use_form_plan(self):
        if self.is_busy(): return
        self._loaded_plan = None
        for widget in self._plan_widgets: widget.setEnabled(True)
        self._invalidate_plan()

    def start_rehearsal(self, path=None):
        return self._start('rehearsal', path)

    def start_live(self, path=None):
        return self._start('live', path)

    def _start(self, mode, path):
        if self.is_busy():
            self.on_error('A finite experiment is already running.'); return False
        if not self._prepared_is_current():
            self.on_error('Prepare a finite waveform with the current payload settings first.'); return False
        try:
            settings = self._settings(require_serial=mode == 'live')
            plan = None
            if mode == 'live':
                if self.validate_plan() is None: return False
                plan = self._connection_plan()
                if self._validated_fingerprint != self._plan_fingerprint(settings, plan):
                    raise ValueError('Plan changed during validation; check it again.')
            path = Path(path) if path is not None else self._choose_new_folder('Choose a new experiment folder', mode)
            if path is None: return False
            if path.exists(): raise FileExistsError('Choose a new experiment folder; existing evidence is preserved.')
            reference = Path(self.tx_reference_path)
            source = json.loads(json.dumps(self.payload_source, allow_nan=False))
            cancellation = threading.Event()
            # Only captured values enter the worker. No QWidget is read there.
            def run():
                from .conducted import run_conducted_experiment
                return run_conducted_experiment(path, tx_reference_path=reference, mode=mode,
                                                settings=settings, connection_plan=plan,
                                                payload_source=source, cancel_event=cancellation)
            self.cancel_event = cancellation
            self._mode = mode
            self.last_result = None
            self._busy = True
            for widget in self._editable: widget.setEnabled(False)
            self.review_checked.setEnabled(False); self.review_btn.setEnabled(False)
            self.status.setText('SOFTWARE REHEARSAL | no hardware access | finite run in progress.' if mode == 'rehearsal'
                                else 'PHYSICAL TX + RX | one finite burst requested | waiting for recorded controller outcome.')
            self.output.setPlainText('Experiment folder: ' + str(path) + '\nStop requests cancellation and controller cleanup. Closing waits for the worker.')
            self.task = _ExperimentTask(run, self)
            self.task.completed.connect(self._completed)
            self.task.failed.connect(self._failed)
            self.task.finished.connect(self._finished)
            self.activity_changed.emit(True)
            self._update_buttons()
            self.task.start()
            return True
        except Exception as exc:
            self.on_error(str(exc)); return False

    def stop(self):
        if self.is_busy() and self.cancel_event is not None:
            self.cancel_event.set()
            self.status.setText('Cancellation requested. Waiting for finite controller cleanup; shutdown is not yet confirmed.')
            self._update_buttons()

    def _require_operator_review(self):
        self._live_review_required = True
        self._validated_fingerprint = None
        self.review_checked.setChecked(False)
        self.review_checked.setVisible(True)
        self.review_btn.setVisible(True)
        self.plan_status.setText('PHYSICAL SHUTDOWN NOT CONFIRMED. Review connections and device state before any live retry; then record review and check a fresh plan. No automatic retry will occur.')

    def _completed(self, result):
        self.last_result = result
        if self._mode == 'live' and result.get('cleanup_confirmed') is not True:
            self._require_operator_review()
        scope = 'SOFTWARE REHEARSAL | no RF transmission' if self._mode == 'rehearsal' else 'PHYSICAL EXPERIMENT | recorded controller outcome'
        cleanup = 'confirmed by recorded controller outcome' if result.get('cleanup_confirmed') is True else 'NOT CONFIRMED'
        self.status.setText(f"{scope} | {result.get('status', 'unknown')} | cleanup {cleanup}")
        displayed = dict(result)
        if result.get('experiment_manifest'):
            try:
                record = json.loads(Path(result['experiment_manifest']).read_text(encoding='utf-8'))
                displayed['raw_capture_paths'] = (record.get('transport') or {}).get('paths', {})
            except (OSError, ValueError, TypeError):
                # Preserve the worker outcome even when an evidence read fails.
                displayed['raw_capture_paths'] = 'See experiment manifest; path details could not be read here.'
        self.output.setPlainText(json.dumps(displayed, indent=2))
        self.experiment_completed.emit(result)

    def _failed(self, error):
        if self._mode == 'live': self._require_operator_review()
        self.status.setText(('PHYSICAL EXPERIMENT | cleanup NOT CONFIRMED. ' if self._mode == 'live' else 'SOFTWARE REHEARSAL failed. ') + error)
        self.output.setPlainText(error)
        self.on_error(error)

    def _finished(self):
        task = self.task
        # finished() precedes the final native thread teardown. Join it before
        # releasing ownership or reporting the panel idle. Never terminate it.
        if task is not None and not task.wait(1000):
            QTimer.singleShot(50, self._finished)
            return
        self.task = None
        self._busy = False
        self.cancel_event = None
        for widget in self._editable: widget.setEnabled(True)
        if self._loaded_plan is not None:
            for widget in self._plan_widgets: widget.setEnabled(False)
        self.review_checked.setEnabled(True); self.review_btn.setEnabled(True)
        self._update_buttons()
        self.activity_changed.emit(False)
        if task is not None: task.deleteLater()

    def record_operator_review(self):
        if self.is_busy(): return False
        if not self.review_checked.isChecked():
            self.on_error('Explicitly record that you reviewed physical connections and device shutdown state before clearing the retry block.'); return False
        self._live_review_required = False
        self.review_checked.setVisible(False); self.review_btn.setVisible(False)
        # Previous declarations do not automatically approve another run.
        self.connection_checked.setChecked(False); self.unused_loaded.setChecked(False)
        self._loaded_plan = None
        for widget in self._plan_widgets: widget.setEnabled(True)
        self._invalidate_plan()
        self.plan_status.setText('Operator review recorded; this does not retroactively confirm cleanup. Recheck connections and a fresh plan before physical TX.')
        return True

    def open_report(self):
        if self.last_result and self.last_result.get('bench_manifest'):
            report = Path(self.last_result['bench_manifest']).parent / 'report.html'
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(report)))

    def show_decoded(self):
        if self.last_result and self.last_result.get('receiver_manifest'):
            try:
                from .storage import load_run
                self.on_result(load_run(Path(self.last_result['receiver_manifest'])))
            except Exception as exc:
                self.on_error(str(exc))
