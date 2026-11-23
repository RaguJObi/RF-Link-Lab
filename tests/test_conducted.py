"""End-to-end finite experiment orchestration, without physical RF hardware."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading

import pytest

from rf_link_lab.bench import BenchSession,verify_bench_session
from rf_link_lab.bladerf_duplex import DuplexSettings
from rf_link_lab.conducted import example_payload,run_conducted_experiment,verify_conducted_experiment
from rf_link_lab.conducted_waveform import prepare_tx_waveform
from rf_link_lab.storage import load_run

SERIAL='0123456789abcdef0123456789abcdef'


def plan():
    return {'schema':'rf-link-lab.conducted-plan.v1','device_serial':SERIAL,
            'connection':'TX1_ATTENUATORS_RX1','operator_checked_connections':True,
            'unused_ports_correctly_loaded':True,'active_antenna_connected':False,'cable_loss_db':0,
            'attenuators':[{'label':'Mock-only fixture','attenuation_db':40,'frequency_min_hz':1000000,'frequency_max_hz':6000000000,'power_rating_w':1}],
            'tx_power_bound':{'upper_dbm':-10,'frequency_hz':915000000,'tx_gain_db':-20,'source_note':'Test fixture assertion only; no physical authorization'},
            'rx_working_ceiling_dbm':-20}


@pytest.fixture
def prepared(tmp_path):return prepare_tx_waveform(example_payload(),tmp_path/'prepared')


def test_offline_experiment_uses_saved_bytes_and_preserves_known_payload(prepared,tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('Offline rehearsal launched a process'))
    result=run_conducted_experiment(tmp_path/'offline',tx_reference_path=prepared,payload_source={'source':'offline_example_payload'})
    assert result['status']=='decoded' and result['mode']=='rehearsal'
    assert result['metrics']['known_payload_frames_matched']==12
    assert result['metrics']['known_payload_frames_missing']==0 and result['metrics']['ber']==0
    verified=verify_conducted_experiment(result['experiment_manifest'])
    assert not verified['manifest']['tx_start_requested']
    assert verified['manifest']['origin']=='synthetic_duplex_rehearsal'
    bench=verify_bench_session(result['bench_manifest'])
    assert bench['evidence_count']==3
    assert all(row['status']=='not_attempted' for row in bench['manifest']['checks'])
    saved=load_run(Path(result['receiver_manifest']))
    assert saved.metadata['diagnosis_allowed'] is False
    assert saved.metadata['original_origin']=='synthetic_duplex_rehearsal'
    assert saved.reference_bits.size==12*17*8
    assert (tmp_path/'offline/tx/tx.sc16q11').read_bytes()==(prepared.parent/'tx.sc16q11').read_bytes()


def test_live_missing_plan_cannot_create_run_or_invoke_transport(prepared,tmp_path):
    calls=[]
    with pytest.raises(ValueError):
        run_conducted_experiment(tmp_path/'forbidden',tx_reference_path=prepared,mode='live',settings=DuplexSettings(SERIAL),duplex_runner=lambda *a,**kw:calls.append(1))
    assert not calls and not (tmp_path/'forbidden').exists()


def test_prepared_sample_rate_mismatch_cannot_start(prepared,tmp_path):
    with pytest.raises(ValueError,match='sample rates'):
        run_conducted_experiment(tmp_path/'forbidden',tx_reference_path=prepared,settings=DuplexSettings('',sample_rate=2000000))
    assert not (tmp_path/'forbidden').exists()


def test_cancel_before_transport_still_yields_truthful_portable_report(prepared,tmp_path):
    event=threading.Event();event.set()
    result=run_conducted_experiment(tmp_path/'cancelled',tx_reference_path=prepared,cancel_event=event)
    record=verify_conducted_experiment(result['experiment_manifest'])['manifest']
    assert record['status']=='cancelled' and record['cleanup_confirmed']
    assert not record['transport_invoked'] and not record['tx_start_requested']
    assert result['receiver_manifest'] is None
    assert verify_bench_session(result['bench_manifest'])['evidence_count']==1


def test_transport_failure_preserves_partial_samples_without_success_claim(prepared,tmp_path):
    def failed(tx,output,settings,**kw):
        output.mkdir();(output/'rx.sc16q11').write_bytes(b'\0'*16)
        (output/'session.log').write_text('Mock USB stream disconnected after TX request')
        return {'status':'failed','capture_completed':False,'tx_start_requested':True,'cleanup_confirmed':False,
                'error':'Mock disconnected','paths':{'rx':str(output/'rx.sc16q11')}}
    result=run_conducted_experiment(tmp_path/'failed',tx_reference_path=prepared,mode='live',settings=DuplexSettings(SERIAL),connection_plan=plan(),duplex_runner=failed)
    record=verify_conducted_experiment(result['experiment_manifest'])['manifest']
    assert result['status']=='failed' and not result['cleanup_confirmed']
    assert 'hardware/rx.sc16q11' in record['assets'] and not result['metrics']
    assert result['receiver_manifest'] is None
    assert verify_bench_session(result['bench_manifest'])['evidence_count']==1


def test_unexpected_transport_failure_marks_tx_and_cleanup_unconfirmed(prepared,tmp_path):
    def unexpected(*args,**kwargs):raise RuntimeError('Unexpected mock failure')
    result=run_conducted_experiment(tmp_path/'unexpected',tx_reference_path=prepared,mode='live',settings=DuplexSettings(SERIAL),connection_plan=plan(),duplex_runner=unexpected)
    record=verify_conducted_experiment(result['experiment_manifest'])['manifest']
    assert record['tx_start_requested'] is None and record['cleanup_confirmed'] is False
    assert 'Unexpected mock failure' in result['error']


def test_experiment_copy_is_portable_and_changes_are_rejected(prepared,tmp_path):
    result=run_conducted_experiment(tmp_path/'offline',tx_reference_path=prepared)
    outer=BenchSession.create(tmp_path/'outer');outer.add_experiment_recording(Path(result['experiment_manifest']));outer.finalize()
    assert verify_bench_session(outer.folder)['evidence_count']==1
    target=tmp_path/'offline/tx/tx.sc16q11'
    with target.open('ab') as stream:stream.write(b'changed')
    with pytest.raises(ValueError,match='hash or size mismatch'):verify_conducted_experiment(result['experiment_manifest'])
    assert verify_bench_session(outer.folder)['evidence_count']==1


def test_experiment_inventory_rejects_path_traversal(prepared,tmp_path):
    result=run_conducted_experiment(tmp_path/'offline',tx_reference_path=prepared)
    manifest=Path(result['experiment_manifest']);record=json.loads(manifest.read_text())
    record['assets']['../outside']={'size_bytes':1,'sha256':'0'*64}
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='Unsafe'):verify_conducted_experiment(manifest)


def test_metrics_and_outcome_must_agree_with_preserved_receiver(prepared,tmp_path):
    result=run_conducted_experiment(tmp_path/'offline',tx_reference_path=prepared)
    manifest=Path(result['experiment_manifest']);record=json.loads(manifest.read_text())
    record['metrics']['known_payload_frames_matched']=999
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='metrics disagree'):verify_conducted_experiment(manifest)
    record['metrics']=json.loads((manifest.parent/'receiver/run.json').read_text())['metrics']
    record['status']='capture_without_complete_payload';manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='outcome disagrees'):verify_conducted_experiment(manifest)


def test_live_tx_bytes_must_match_the_reference_before_decode(prepared,tmp_path):
    def changed(tx,output,settings,**kw):
        output.mkdir();offered=output/'tx.sc16q11';offered.write_bytes(b'changed')
        return {'status':'complete','capture_completed':True,'cleanup_confirmed':True,'tx_start_requested':True,
                'tx_sha256':hashlib.sha256(b'changed').hexdigest(),'paths':{'tx':str(offered)}}
    result=run_conducted_experiment(tmp_path/'changed-tx',tx_reference_path=prepared,mode='live',
                                    settings=DuplexSettings(SERIAL),connection_plan=plan(),duplex_runner=changed)
    assert result['status']=='failed' and not result['metrics']
    assert 'differs from' in result['error']
    assert verify_conducted_experiment(result['experiment_manifest'])['status']=='verified'


def test_cli_prepare_rehearse_verify_and_missing_live_plan(tmp_path,capsys,monkeypatch):
    from rf_link_lab.cli import main
    import subprocess
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('CLI offline workflow launched a process'))
    prepared=tmp_path/'prepared';output=tmp_path/'offline'
    assert main(['link-prepare','--output',str(prepared)])==0
    assert 'Prepared finite bytes' in capsys.readouterr().out
    assert main(['link-rehearse',str(prepared),'--output',str(output)])==0
    assert json.loads(capsys.readouterr().out)['status']=='decoded'
    assert main(['link-verify',str(output)])==0
    assert json.loads(capsys.readouterr().out)['status']=='verified'
    incomplete=tmp_path/'incomplete-plan.json';incomplete.write_text('{}')
    with pytest.raises(SystemExit) as exc:main(['link-live',str(prepared),'--output',str(tmp_path/'forbidden'),'--plan',str(incomplete)])
    assert exc.value.code==2 and not (tmp_path/'forbidden').exists()
