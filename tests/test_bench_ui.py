"""Exercise logging through our native widgets; no OS input or USB access."""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from rf_link_lab.bench import verify_bench_session
from rf_link_lab.serial_logging import replay_uart_recording
from rf_link_lab.ui import Window


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_gui_records_offline_bytes_observations_and_finalizes(app,tmp_path,monkeypatch):
    import serial
    monkeypatch.setattr(serial,'Serial',lambda **kwargs:pytest.fail('Offline workflow opened a port'))
    window=Window();panel=window.hardware_panel
    folder=tmp_path/'bench'
    assert panel.start_bench(folder)==folder
    original=panel.session
    panel.serial_demo()
    assert panel.session is original
    assert panel.session.snapshot()['recording']['status']=='active'
    panel.bench_note.setText('<b>Operator note remains literal</b>');panel.add_bench_note()
    assert '<b>Operator note remains literal</b>' in panel.bench_output.toPlainText()
    panel.bench_check.setCurrentIndex(3);panel.bench_check_status.setCurrentText('not_attempted')
    panel.bench_check_details.setText('No board attached');panel.record_bench_check()
    manifest=panel.finish_bench()
    verified=verify_bench_session(manifest)
    assert verified['evidence_count']==1
    assert verified['manifest']['evidence'][0]['summary']['sources']==['offline_feed']
    assert all(row['status']=='not_attempted' for row in verified['manifest']['checks'])
    assert replay_uart_recording(folder/'uart')['matches']
    assert panel.last_bench_report.is_file() and panel.bench is None
    window.close()


def test_disconnect_does_not_end_bench_recording_and_close_saves_it(app,tmp_path):
    window=Window();panel=window.hardware_panel;folder=tmp_path/'bench'
    panel.start_bench(folder);panel.serial_demo();panel.disconnect_serial()
    assert panel.session.snapshot()['recording']['status']=='active'
    panel.serial_demo()
    assert panel.session.snapshot()['packets_ok']==12
    window.close();app.processEvents()
    result=replay_uart_recording(folder/'uart')
    assert result['matches'] and result['recorded_summary']['packets_ok']==12
    assert verify_bench_session(folder)['evidence_count']==1


def test_gui_incomplete_capture_stays_visible_and_is_preserved(app,tmp_path,monkeypatch):
    from rf_link_lab.serial_link import SerialTelemetrySession
    original=SerialTelemetrySession.start_recording
    def limited(self,path):return original(self,path,max_raw_bytes=20)
    monkeypatch.setattr(SerialTelemetrySession,'start_recording',limited)
    window=Window();panel=window.hardware_panel;folder=tmp_path/'bench'
    panel.start_bench(folder);session=panel.session;panel.serial_demo()
    assert panel.session.snapshot()['recording']['status']=='error'
    assert 'Recording error' in panel.bench_status.text()
    panel.serial_demo()
    assert panel.session is session
    manifest=panel.finish_bench()
    evidence=verify_bench_session(manifest)['manifest']['evidence'][0]
    assert not evidence['complete'] and evidence['status']=='incomplete'
    assert 'limit' in evidence['error']
    assert 'INCOMPLETE' in panel.last_bench_report.read_text(encoding='utf-8')
    window.close()


def test_gui_refuses_to_arm_mid_connection_without_creating_folder(app,tmp_path):
    window=Window();panel=window.hardware_panel;errors=[];panel.on_error=errors.append
    panel.session._connected=True
    assert panel.start_bench(tmp_path/'not-created') is None
    assert not (tmp_path/'not-created').exists()
    assert errors and 'Disconnect' in errors[-1]
    window.close()


def test_gui_creates_portable_demo_and_attaches_iq(app,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    window=Window();panel=window.hardware_panel
    demo=panel.create_bench_demo(tmp_path/'demo')
    assert verify_bench_session(demo)['evidence_count']==2
    panel.start_bench(tmp_path/'bench')
    iq=demo.parent/'synthetic-iq/run.json'
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *args,**kw:(str(iq),''))
    panel.attach_bench_iq()
    manifest=panel.finish_bench()
    records=verify_bench_session(manifest)['manifest']['evidence']
    assert any(row['kind']=='iq' and row['summary']['declared_origin']=='synthetic' for row in records)
    window.close()


def test_finite_link_attaches_to_active_bench_and_blocks_other_radio_operations(app,tmp_path,monkeypatch):
    import time
    import rf_link_lab.hardware as hardware
    monkeypatch.setattr(hardware,'probe_bladerf',lambda:pytest.fail('Overlapping device operation'))
    window=Window();panel=window.hardware_panel;errors=[];panel.on_error=errors.append
    panel.start_bench(tmp_path/'bench')
    link=panel.conducted_panel
    assert link.prepare_waveform(tmp_path/'tx')
    assert link.start_rehearsal(tmp_path/'link')
    assert not panel.probe_btn.isEnabled() and not panel.finish_bench_btn.isEnabled()
    panel.probe();assert panel.finish_bench() is None
    deadline=time.monotonic()+20
    while link.is_busy() and time.monotonic()<deadline:
        app.processEvents();time.sleep(.005)
    assert not link.is_busy() and link.last_result['status']=='decoded'
    assert errors and panel.probe_btn.isEnabled()
    link.show_decoded()
    assert window.diagnosis is None and 'Known-payload' in window.ai_label.text()
    assert 'REHEARSAL' in window.status.text()
    manifest=panel.finish_bench()
    verified=verify_bench_session(manifest)
    experiments=[item for item in verified['manifest']['evidence'] if item['kind']=='experiment']
    assert len(experiments)==1 and experiments[0]['summary']['experiment_status']=='decoded'
    assert experiments[0]['summary']['tx_start_requested'] is False
    window.close()
