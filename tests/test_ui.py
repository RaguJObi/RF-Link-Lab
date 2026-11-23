"""Exercise our own Qt widgets offscreen; no operating-system input automation."""
import os
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from rf_link_lab.ui import Window


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_workbench_controls_and_learning_explanation(app):
    w=Window();w.show();app.processEvents()
    w.select_page(1);w.preset.setCurrentIndex(2);w.run_current()
    assert w.result.config.snr_db==2
    assert w.result.config.clip_level==4
    assert w.result.metrics['packets_ok'] < w.result.metrics['packets_total']
    assert w.metric_labels['packet_success_rate'].text() != '—'
    w.select_page(0);w.show_lesson(1)
    assert w.lesson_answer.isHidden()
    w.reveal.click();assert not w.lesson_answer.isHidden()
    assert '100,000' in w.lesson_answer.text()
    w.close()


def test_fault_controls_do_not_access_hardware(app,monkeypatch):
    import rf_link_lab.hardware as hardware
    monkeypatch.setattr(hardware,'probe_bladerf',lambda: (_ for _ in ()).throw(AssertionError('automatic probe')))
    w=Window();w.run_current()
    assert w.result.metrics['packets_ok']==w.result.metrics['packets_total']
    for i in range(5):w.select_page(i)
    assert 'Not probed' in w.hardware_result.toPlainText()
    w.close()


def test_replay_restores_controls_and_preserves_recorded_inference(app,tmp_path,monkeypatch):
    from rf_link_lab.simulation import SimulationConfig,run_simulation
    from rf_link_lab.storage import save_run
    from PySide6.QtWidgets import QFileDialog
    recorded={'label':'clipping','support':.91,'probabilities':{'clipping':.91},'evidence':['recorded evidence'],'next_measurement':'recorded next test','unknown_reason':None}
    path=save_run(run_simulation(SimulationConfig(seed=61,snr_db=27,gain=3.6)),tmp_path,recorded)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a,**kw:(str(path),''))
    w=Window();w.open_dialog()
    assert w.gain.value()==3.6 and w.seed.value()==61
    assert w.diagnosis==recorded
    assert 'RECORDED INFERENCE' in w.ai_evidence.text()
    assert 'HASH VERIFIED' in w.status.text()
    w.run_current()
    assert 'HASH VERIFIED' not in w.status.text()
    w.close()


def test_offline_serial_and_independent_receiver_ui(app):
    w=Window();w.show();app.processEvents()
    w.hardware_panel.serial_demo()
    status=w.hardware_panel.session.snapshot()
    assert status['input_source']=='offline_feed' and status['packets_ok']==6
    assert 'offline_feed' in w.hardware_panel.serial_status.text()
    w.hardware_panel.serial_waveform()
    assert w.result.metrics['packets_ok']==6
    assert w.result.metadata['provenance']=='serial_waveform_software_loopback'
    assert w.diagnosis is None
    assert 'NO RF TRANSMISSION' in w.status.text()
    w.close()


def test_reference_free_capture_gates_model_and_metrics(app):
    from rf_link_lab.waveform import generate_waveform
    from rf_link_lab.protocol import Telemetry
    from rf_link_lab.receiver import decode_iq
    wave=generate_waveform([Telemetry(5,900,True)])
    result=decode_iq(wave.tx_iq,wave.metadata)
    w=Window();w.apply_external_result(result)
    assert w.metric_labels['ber'].text()=='Unavailable'
    assert w.metric_labels['evm_pct'].text()=='Unavailable'
    assert w.diagnosis is None
    assert not w.follow_btn.isEnabled()
    w.close()
