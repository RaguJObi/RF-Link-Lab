"""Explicit hardware controls and offline exercise of the same serial pipeline."""
from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path

from PySide6.QtCore import Qt,QThread,Signal,QTimer,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTabWidget,
    QComboBox,QSpinBox,QTextBrowser,QFileDialog,QCheckBox,QFrame,QLineEdit)

from .paths import resource_root,state_root
from .serial_link import SerialTelemetrySession,list_ports
from .protocol import Telemetry,encode_telemetry


def text(value,kind=None):
    w=QLabel(value);w.setWordWrap(True)
    if kind:w.setObjectName(kind)
    return w


class Task(QThread):
    completed=Signal(object)
    failed=Signal(str)
    def __init__(self,fn):super().__init__();self.fn=fn
    def run(self):
        try:self.completed.emit(self.fn())
        except Exception as exc:self.failed.emit(f'{type(exc).__name__}: {exc}')


class HardwarePanel(QWidget):
    def __init__(self,on_recording,on_serial_waveform,on_error):
        super().__init__();self.on_recording=on_recording;self.on_serial_waveform=on_serial_waveform;self.on_error=on_error
        self.session=SerialTelemetrySession();self.task=None;self.last_capture=None
        self.bench=None;self.last_bench_report=None;self.last_bench_manifest=None
        lay=QVBoxLayout(self);lay.setContentsMargins(0,0,0,0);lay.setSpacing(14)
        lay.addWidget(text('Connect the boards. Verify one boundary at a time.','title'))
        lay.addWidget(text('Ubuntu hosts both USB boards. Start with serial telemetry and RX checks, then rehearse or explicitly start a finite conducted link in tab 5.','muted'))
        tabs=QTabWidget();self.tabs=tabs;lay.addWidget(tabs)
        setup=QWidget();sl=QVBoxLayout(setup);sl.setSpacing(12)
        sl.addWidget(text('FIRST CONNECTION','eyebrow'))
        sl.addWidget(text('1. Run SETUP-UBUNTU.sh once, then sign out and back in.\n2. Connect the Nucleo at its ST-LINK USB connector and flash the included .bin once.\n3. Connect bladeRF to USB 3.\n4. Select the Nucleo serial port on the next tab and check sequence/button updates.\n5. Identify bladeRF, then make a finite RX recording.'))
        row=QHBoxLayout();b=QPushButton('Check local setup');b.clicked.connect(self.doctor);row.addWidget(b)
        self.probe_btn=QPushButton('Identify bladeRF');self.probe_btn.clicked.connect(self.probe);row.addWidget(self.probe_btn)
        b=QPushButton('Open connection guide');b.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_root()/'docs/CONNECT-AND-RUN.md'))));row.addWidget(b)
        b=QPushButton('Open firmware folder');b.clicked.connect(self.open_firmware);row.addWidget(b);sl.addLayout(row)
        self.setup_output=QTextBrowser();self.setup_output.setMinimumHeight(250);self.setup_output.setPlainText('Not probed. No device has been opened. Use an explicit check when the boards are connected.');sl.addWidget(self.setup_output)
        tabs.addTab(setup,'1 · Setup & identity')
        serial=QWidget();pl=QVBoxLayout(serial);pl.setSpacing(12)
        pl.addWidget(text('STM32 TELEMETRY','eyebrow'));row=QHBoxLayout();self.ports=QComboBox();self.ports.setEditable(True);self.ports.setMinimumWidth(250);row.addWidget(self.ports,1)
        b=QPushButton('Refresh ports');b.clicked.connect(self.refresh_ports);row.addWidget(b)
        self.connect_btn=QPushButton('Connect selected port');self.connect_btn.setObjectName('primary');self.connect_btn.clicked.connect(self.connect_serial);row.addWidget(self.connect_btn)
        b=QPushButton('Disconnect');b.clicked.connect(self.disconnect_serial);row.addWidget(b);pl.addLayout(row)
        pl.addWidget(text('115200 baud · 8 data bits · no parity · 1 stop bit · binary 17-byte telemetry. Refresh lists ports without opening them.','muted'))
        self.serial_status=text('Disconnected. No serial port has been opened.');pl.addWidget(self.serial_status)
        self.serial_output=QTextBrowser();self.serial_output.setMinimumHeight(210);pl.addWidget(self.serial_output)
        row=QHBoxLayout();b=QPushButton('Exercise serial parser without a board');b.clicked.connect(self.serial_demo);row.addWidget(b)
        b=QPushButton('Decode these packets through software modem');b.clicked.connect(self.serial_waveform);row.addWidget(b);pl.addLayout(row)
        pl.addWidget(text('The software modem action uses received telemetry bytes but does not send them over RF. Its source is labelled in the saved report.','muted'))
        tabs.addTab(serial,'2 · Nucleo serial')
        capture=QWidget();cl=QVBoxLayout(capture);cl.setSpacing(12)
        cl.addWidget(text('FINITE RECEIVE-ONLY CAPTURE','eyebrow'));row=QHBoxLayout()
        self.frequency=self.spin(1000000000,70000000,6000000000);self.rate=self.spin(1000000,1000000,10000000);self.count=self.spin(200000,1024,2000000);self.gain=self.spin(0,-5,50)
        for name,widget in [('Center frequency (Hz)',self.frequency),('Samples/s',self.rate),('Complex samples',self.count),('RX gain (dB)',self.gain)]:
            col=QVBoxLayout();col.addWidget(text(name,'muted'));col.addWidget(widget);row.addLayout(col)
        cl.addLayout(row)
        self.rx_ack=QCheckBox('RX connection and external input level checked; no transmitter is enabled.');cl.addWidget(self.rx_ack)
        row=QHBoxLayout();self.capture_btn=QPushButton('Record RX to a new file');self.capture_btn.setObjectName('primary');self.capture_btn.clicked.connect(self.capture);row.addWidget(self.capture_btn)
        b=QPushButton('Import and decode an I/Q file');b.clicked.connect(self.import_recording);row.addWidget(b);cl.addLayout(row)
        self.use_reference=QCheckBox('Use a saved synthetic payload reference for benchmark BER/EVM');cl.addWidget(self.use_reference)
        cl.addWidget(text('Reference waveform: RF Link Lab QPSK at 50,000 symbols/s. Raw capture input rate comes from the Samples/s control; decoding resamples explicitly to 200,000 samples/s. Ambient RF can be inspected but is not expected to decode as our telemetry.','muted'))
        self.capture_output=QTextBrowser();self.capture_output.setMinimumHeight(220);self.capture_output.setPlainText('No RX recording has been made.');cl.addWidget(self.capture_output)
        tabs.addTab(capture,'3 · RX & recording replay')
        bench=QWidget();bl=QVBoxLayout(bench);bl.setSpacing(10)
        bl.addWidget(text('REPRODUCIBLE BENCH RECORD','eyebrow'))
        bl.addWidget(text('Start before connecting serial. Exact UART bytes and packet events are saved until you finish the session. Offline input remains labelled offline.','muted'))
        row=QHBoxLayout();self.bench_title=QLineEdit('First hardware session');self.bench_title.setMaxLength(160);row.addWidget(self.bench_title,1)
        self.begin_bench_btn=QPushButton('Start bench session');self.begin_bench_btn.setObjectName('primary');self.begin_bench_btn.clicked.connect(lambda:self.start_bench());row.addWidget(self.begin_bench_btn)
        self.finish_bench_btn=QPushButton('Finish && save report');self.finish_bench_btn.clicked.connect(self.finish_bench);row.addWidget(self.finish_bench_btn)
        self.open_bench_btn=QPushButton('Open last report');self.open_bench_btn.clicked.connect(self.open_bench_report);row.addWidget(self.open_bench_btn);bl.addLayout(row)
        row=QHBoxLayout();self.bench_note=QLineEdit();self.bench_note.setMaxLength(4000);self.bench_note.setPlaceholderText('Observation, cable arrangement, board revision or next measurement…');row.addWidget(self.bench_note,1)
        b=QPushButton('Add note');b.clicked.connect(self.add_bench_note);row.addWidget(b);bl.addLayout(row)
        row=QHBoxLayout();self.bench_check=QComboBox()
        for name,key in [('Ubuntu launch','ubuntu_execution'),('Nucleo identity','nucleo_identity'),('Firmware flash','firmware_flash'),('Physical button response','uart_button_response'),('bladeRF identity','bladerf_identification'),('bladeRF RX streaming','bladerf_rx_capture'),('Conducted RF link','conducted_rf_link')]:self.bench_check.addItem(name,key)
        row.addWidget(self.bench_check,1);self.bench_check_status=QComboBox();self.bench_check_status.addItems(['not_attempted','passed','failed']);row.addWidget(self.bench_check_status)
        self.bench_check_details=QLineEdit();self.bench_check_details.setPlaceholderText('Evidence or reason for this observation');self.bench_check_details.setMaxLength(2000);row.addWidget(self.bench_check_details,2)
        b=QPushButton('Record observation');b.clicked.connect(self.record_bench_check);row.addWidget(b);bl.addLayout(row)
        bl.addWidget(text('These check marks are operator observations. Recording bytes, reading device identity and proving an RF link are separate results.','muted'))
        self.bench_status=text('No bench session is active.');bl.addWidget(self.bench_status)
        self.bench_output=QTextBrowser();self.bench_output.setMinimumHeight(180);self.bench_output.setPlainText('Create an offline example to try logging, verified replay and the portable report without opening a device.');bl.addWidget(self.bench_output,1)
        row=QHBoxLayout();b=QPushButton('Attach saved I/Q evidence');b.clicked.connect(self.attach_bench_iq);row.addWidget(b)
        b=QPushButton('Create offline bench example');b.clicked.connect(lambda:self.create_bench_demo());row.addWidget(b);bl.addLayout(row)
        bl.addWidget(text('Finishing disconnects serial and closes the log. The report folder contains copies of linked recordings, hashes and a readable report.html.','muted'))
        tabs.addTab(bench,'4 · Bench session')
        from .conducted_ui import ConductedPanel
        self.conducted_panel=ConductedPanel(self.session_snapshot,self.on_recording,self.on_error)
        self.conducted_panel.activity_changed.connect(self._conducted_activity)
        self.conducted_panel.experiment_completed.connect(self._attach_experiment)
        tabs.addTab(self.conducted_panel,'5 · Finite TX / RX')
        from .hardware_review_ui import HardwareReviewPanel
        self.hardware_review_panel=HardwareReviewPanel(self.on_error)
        self.hardware_review_panel.activity_changed.connect(self._review_activity)
        tabs.addTab(self.hardware_review_panel,'6 · Evidence & AI')
        self.timer=QTimer(self);self.timer.setInterval(200);self.timer.timeout.connect(self.refresh_serial_view);self.timer.start()
        self.refresh_serial_view()

    @staticmethod
    def spin(value,minimum,maximum):
        # Qt integer spinboxes are signed32bit; frequencies use scaled MHz? Use a double for Hz.
        from PySide6.QtWidgets import QDoubleSpinBox
        w=QDoubleSpinBox();w.setDecimals(0);w.setRange(minimum,maximum);w.setValue(value);w.setSingleStep(1);return w

    def busy(self,fn,callback):
        if self.hardware_review_panel.is_busy():
            self.on_error('Wait for the saved-evidence review or model job to finish.');return
        if self.conducted_panel.is_busy():
            self.on_error('Wait for the finite link experiment and radio cleanup to finish.');return
        if self.task is not None and self.task.isRunning():
            self.on_error('Wait for the current hardware operation to finish.');return
        self.probe_btn.setEnabled(False);self.capture_btn.setEnabled(False)
        self.conducted_panel.setEnabled(False)
        self.hardware_review_panel.setEnabled(False)
        self.task=Task(fn);self.task.completed.connect(callback);self.task.failed.connect(self.on_error)
        self.task.finished.connect(self._operation_finished);self.task.start()

    def session_snapshot(self):return self.session.snapshot()

    def _operation_finished(self):
        self.probe_btn.setEnabled(True);self.capture_btn.setEnabled(True)
        self.conducted_panel.setEnabled(not self.hardware_review_panel.is_busy())
        self.hardware_review_panel.setEnabled(True);self.refresh_bench_view()

    def _conducted_activity(self,active):
        self.probe_btn.setEnabled(not active);self.capture_btn.setEnabled(not active)
        if hasattr(self,'hardware_review_panel'):
            self.hardware_review_panel.setEnabled(not active)
        self.refresh_bench_view()

    def _review_activity(self,active):
        hardware_busy=(self.task is not None and self.task.isRunning())
        available=not active and not hardware_busy and not self.conducted_panel.is_busy()
        self.probe_btn.setEnabled(available);self.capture_btn.setEnabled(available)
        self.conducted_panel.setEnabled(not active and not hardware_busy)

    def _attach_experiment(self,result):
        if self.bench is None:return
        try:
            self.bench.add_experiment_recording(Path(result['experiment_manifest']))
            self._bench_message('Finite link experiment copied and linked: '+result['experiment_manifest'])
        except Exception as exc:self.on_error('Experiment saved, but bench attachment failed: '+str(exc))

    def doctor(self):
        from .preflight import doctor
        self.setup_output.setPlainText(json.dumps(doctor(),indent=2))

    def probe(self):
        from .hardware import probe_bladerf
        self.setup_output.setPlainText('Querying bladeRF identity and versions…')
        def completed(result):
            self.setup_output.setPlainText(json.dumps(result,indent=2))
            if self.bench is not None:
                try:self.bench.record_probe(result)
                except Exception as exc:self.on_error(f'Device result obtained, but bench evidence could not be saved: {exc}')
        self.busy(probe_bladerf,completed)

    def open_firmware(self):
        candidates=[resource_root()/'firmware',resource_root()/'artifacts/firmware']
        folder=next((p for p in candidates if (p/'rf_link_lab_f446re.bin').exists()),candidates[0])
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def refresh_ports(self):
        try:
            old=self.ports.currentText().split(' · ')[0].strip();self.ports.clear()
            for port in list_ports():self.ports.addItem(f"{port['device']} · {port['description']}",port['device'])
            found=self.ports.findData(old)
            if found>=0:self.ports.setCurrentIndex(found)
            elif old or not self.ports.count():self.ports.setEditText(str(old) if old else '')
        except Exception as exc:self.on_error(str(exc))

    def connect_serial(self):
        port=self.ports.currentText().split(' · ')[0].strip()
        if not port:self.on_error('Refresh ports and choose the Nucleo ST-LINK virtual serial port.');return
        try:self.session.start(port,115200)
        except Exception as exc:self.on_error(str(exc))
        self.refresh_serial_view()

    def disconnect_serial(self):
        try:self.session.stop()
        except Exception as exc:self.on_error(str(exc))
        self.refresh_serial_view()

    def serial_demo(self):
        if self.session.snapshot()['connected']:
            self.on_error('Disconnect the physical serial session before feeding offline demo bytes.');return
        from .serial_link import demo_serial_session
        try:
            self.session.stop()
            recording=self.session.snapshot().get('recording') or {}
            if self.bench is None and recording.get('status')!='active':self.session=SerialTelemetrySession()
            demo_serial_session(session=self.session)
        except Exception as exc:self.on_error(str(exc));return
        self.refresh_serial_view()

    def refresh_serial_view(self):
        s=self.session.snapshot()
        self.serial_status.setText(f"{s['status']} · source: {s.get('input_source','none')} · accumulated valid packets {s['packets_ok']} · CRC rejected {s['crc_errors']} · missing sequences {s['sequence_gaps']} · duplicates {s['duplicates']} · out of order {s['out_of_order']}"+(f"\n{s['last_error']}" if s.get('last_error') else ''))
        rows=[row for row in s.get('recent',[]) if row.get('session_id')==s.get('session_id') and row.get('source')==s.get('input_source')][-10:]
        self.serial_output.setPlainText('\n'.join(f"sequence={r['sequence']}  uptime={r['uptime_ms']} ms  button={r['button']}  {r.get('sequence_status','')}" for r in rows) or 'Waiting for CRC-valid telemetry.')
        recording=s.get('recording') or {}
        if recording:
            self.serial_status.setText(self.serial_status.text()+f"\nUART log: {recording.get('status')} · {recording.get('bytes_written',0)} bytes saved"+(f" · {recording['last_error']}" if recording.get('last_error') else ''))
        self.refresh_bench_view()

    def _bench_idle(self):
        if self.conducted_panel.is_busy():
            self.on_error('Wait for the finite link experiment before changing the bench session.');return False
        if self.task is not None and self.task.isRunning():
            self.on_error('Wait for the current hardware operation before changing the bench session.');return False
        return True

    def _new_bench_path(self,title):
        from uuid import uuid4
        root=state_root()/'artifacts/bench';root.mkdir(parents=True,exist_ok=True)
        path,_=QFileDialog.getSaveFileName(self,title,str(root/f'session-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}'),'New session folder (*)')
        return Path(path) if path else None

    def start_bench(self,path=None):
        if not self._bench_idle():return None
        if self.bench is not None:self.on_error('Finish the active bench session before starting another.');return None
        if self.session.snapshot()['connected']:
            self.on_error('Disconnect serial before starting a bench session, then reconnect so all bytes are captured.');return None
        path=Path(path) if path is not None else self._new_bench_path('Choose a new bench session folder')
        if path is None:return None
        try:
            from .bench import BenchSession
            from .preflight import doctor
            self.bench=BenchSession.create(path,title=self.bench_title.text().strip() or 'Bench session',environment=doctor())
            self.session.start_recording(self.bench.folder/'uart')
            self.bench_output.setPlainText('Session folder: '+str(self.bench.folder)+'\n\nUART recording is armed. Connect serial in tab 2, or exercise the offline parser. Device queries and RX recordings made here will join this report. Add observations as you work.')
            self.tabs.setCurrentIndex(3);self.refresh_serial_view();return self.bench.folder
        except Exception as exc:
            if self.bench is not None:
                try:self.bench.add_note('UART recording failed to start: '+str(exc))
                except Exception:pass
            self.on_error(f'Bench session could not start: {exc}');self.refresh_bench_view();return None

    def _bench_message(self,message):
        self.bench_output.setPlainText(self.bench_output.toPlainText()+'\n'+message)

    def refresh_bench_view(self):
        if not hasattr(self,'bench_status'):return
        snapshot=self.session.snapshot();recording=snapshot.get('recording') or {}
        active=self.bench is not None
        busy=self.conducted_panel.is_busy() or (self.task is not None and self.task.isRunning())
        self.begin_bench_btn.setEnabled(not active and not busy);self.finish_bench_btn.setEnabled(active and not busy);self.open_bench_btn.setEnabled(self.last_bench_report is not None)
        if active:
            self.bench_status.setText(f"Bench active · source: {snapshot.get('input_source') or 'waiting'} · UART log {recording.get('status','not started')} · {recording.get('bytes_written',0)} bytes · {recording.get('packets_written',0)} packet events"+(f"\nRecording error: {recording['last_error']}" if recording.get('last_error') else ''))
        elif self.last_bench_report:self.bench_status.setText('Report saved: '+str(self.last_bench_report))
        else:self.bench_status.setText('No bench session is active.')

    def add_bench_note(self):
        if not self._bench_idle():return
        if self.bench is None:self.on_error('Start a bench session before adding observations.');return
        note=self.bench_note.text().strip()
        if not note:self.on_error('Enter an observation first.');return
        try:self.bench.add_note(note);self.bench_note.clear();self._bench_message('Note saved: '+note)
        except Exception as exc:self.on_error(str(exc))

    def record_bench_check(self):
        if not self._bench_idle():return
        if self.bench is None:self.on_error('Start a bench session before recording an observation.');return
        try:
            name=self.bench_check.currentData();status=self.bench_check_status.currentText();details=self.bench_check_details.text().strip()
            self.bench.record_check(name,status,details or None)
            self._bench_message(f'Operator observation: {self.bench_check.currentText()} = {status}. {details}')
            self.bench_check_details.clear();self.bench_check_status.setCurrentIndex(0)
        except Exception as exc:self.on_error(str(exc))

    def attach_bench_iq(self):
        if not self._bench_idle():return
        if self.bench is None:self.on_error('Start a bench session before attaching evidence.');return
        path,_=QFileDialog.getOpenFileName(self,'Attach saved I/Q evidence',str(self.last_capture or resource_root()/'artifacts/reference'),'Saved run or raw I/Q (run.json *.sc16q11 *.iq *.bin)')
        if not path:return
        try:self.bench.add_iq_recording(Path(path));self._bench_message('I/Q evidence copied and linked: '+path)
        except Exception as exc:self.on_error(str(exc))

    def finish_bench(self):
        if not self._bench_idle():return None
        if self.bench is None:self.on_error('No bench session is active.');return None
        try:
            self.session.stop()
            recording=self.session.stop_recording()
            if recording:
                try:self.bench.add_uart_recording(Path(recording['output_dir']))
                except Exception as exc:self.bench.add_note('UART evidence attachment failed integrity/completeness validation: '+str(exc))
            manifest=self.bench.finalize()
            data=json.loads(Path(manifest).read_text(encoding='utf-8'))
            summaries=[]
            for item in data.get('evidence',[]):
                if item.get('kind')=='uart':
                    values=item['summary']
                    summaries.append(f"UART file retention: {item['status']} · source: {', '.join(values.get('sources',[])) or 'no bytes received'}\nValid packets: {values['packets_ok']} · CRC rejected: {values['crc_errors']} · observed sequence gaps: {values['sequence_gaps']} · duplicates: {values['duplicates']} · out of order: {values['out_of_order']}\nStream failures: {item.get('stream_error_count',0)}"+(f"\nRecording error: {item['error']}" if item.get('error') else ''))
            self.last_bench_manifest=Path(manifest);self.last_bench_report=Path(manifest).parent/'report.html';self.bench=None
            self.bench_output.setPlainText('Session finalized.\n\n'+'\n\n'.join(summaries)+'\n\nReport: '+str(self.last_bench_report)+'\nManifest: '+str(self.last_bench_manifest)+'\n\nRecorded bytes and linked recordings remain in the session folder. Operator observations are listed separately from measured evidence.')
            self.refresh_serial_view();return self.last_bench_manifest
        except Exception as exc:self.on_error(f'Bench report could not be finalized: {exc}');self.refresh_bench_view();return None

    def create_bench_demo(self,path=None):
        if not self._bench_idle():return None
        if self.bench is not None or self.session.snapshot()['connected']:
            self.on_error('Finish the active bench session and disconnect serial before creating the offline example.');return None
        path=Path(path) if path is not None else self._new_bench_path('Choose a new offline example folder')
        if path is None:return None
        try:
            from .bench import demo_bench_session
            manifest=Path(demo_bench_session(path));self.last_bench_manifest=manifest;self.last_bench_report=manifest.parent/'report.html'
            self.bench_output.setPlainText('Offline example saved: '+str(manifest.parent)+'\n\nUART corruption/recovery was recorded and can be replayed. The attached I/Q is synthetic. No board was opened, flashed or RF-tested. Open the report to inspect the evidence.')
            self.refresh_bench_view();return manifest
        except Exception as exc:self.on_error(str(exc));return None

    def open_bench_report(self):
        if self.last_bench_report is not None:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_bench_report)))

    def serial_waveform(self):
        snapshot=self.session.snapshot()
        rows=[row for row in snapshot.get('recent',[]) if row.get('session_id')==snapshot.get('session_id') and row.get('source')==snapshot.get('input_source')][-12:]
        if not rows:self.on_error('Receive telemetry or exercise the offline parser first.');return
        try:
            from .waveform import generate_waveform
            from .receiver import decode_iq
            messages=[Telemetry(r['sequence'],r['uptime_ms'],r['button']) for r in rows]
            waveform=generate_waveform(messages)
            meta={**waveform.metadata,'provenance':'serial_waveform_software_loopback','original_origin':'serial_waveform_software_loopback','serial_source':snapshot.get('input_source'),'serial_session_id':snapshot.get('session_id'),'rf_transmitted':False}
            self.on_serial_waveform(decode_iq(waveform.tx_iq,meta,reference=waveform))
        except Exception as exc:self.on_error(str(exc))

    def capture(self):
        if not self.rx_ack.isChecked():self.on_error('Check the receive-only connection acknowledgement first.');return
        default=state_root()/'artifacts/hardware';default.mkdir(parents=True,exist_ok=True)
        name=str(default/f'rx-{datetime.now():%Y%m%d-%H%M%S}.sc16q11')
        path,_=QFileDialog.getSaveFileName(self,'Save a new RX recording',name,'SC16 Q11 (*.sc16q11)')
        if not path:return
        from .hardware import capture_rx
        settings=dict(frequency_hz=int(self.frequency.value()),sample_rate=int(self.rate.value()),samples=int(self.count.value()),gain_db=int(self.gain.value()))
        self.capture_output.setPlainText('Receiving a finite recording. No TX is enabled…')
        def completed(result):
            self.last_capture=Path(path);self.capture_output.setPlainText(json.dumps(result,indent=2)+'\n\nRecording saved. Use Import and decode to reprocess it; inspect actual sampling-rate readback first.')
            if self.bench is not None:
                try:self.bench.add_iq_recording(Path(path))
                except Exception as exc:self.on_error(f'RX file saved, but bench attachment failed: {exc}')
        self.busy(lambda:capture_rx(Path(path),**settings),completed)

    def import_recording(self):
        path,_=QFileDialog.getOpenFileName(self,'Import a saved run or raw I/Q',str(self.last_capture or resource_root()/'artifacts/reference'),'Run manifest or SC16 Q11 (run.json *.sc16q11 *.iq *.bin)')
        if not path:return
        from .recordings import decode_recording
        is_manifest=Path(path).name=='run.json'
        rate=None if is_manifest else int(self.rate.value());reference=self.use_reference.isChecked()
        self.busy(lambda:decode_recording(Path(path),sample_rate=rate,use_saved_reference=reference),self.on_recording)

    def shutdown(self):
        if not self.hardware_review_panel.shutdown():return False
        if not self.conducted_panel.shutdown():return False
        if self.task is not None and self.task.isRunning():return False
        if self.bench is not None and self.finish_bench() is None:return False
        try:self.session.stop();self.session.stop_recording()
        except Exception as exc:self.on_error(str(exc));return False
        self.timer.stop();return True
