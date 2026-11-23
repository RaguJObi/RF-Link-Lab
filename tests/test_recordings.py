import hashlib
import json
import numpy as np
import pytest

from rf_link_lab.simulation import run_simulation,SimulationConfig
from rf_link_lab.storage import save_run,load_run
from rf_link_lab.recordings import decode_recording
from rf_link_lab.protocol import Telemetry
from rf_link_lab.waveform import generate_waveform
from rf_link_lab.receiver import decode_iq


def test_saved_samples_are_redecoded_without_generator(tmp_path,monkeypatch):
    path=save_run(run_simulation(SimulationConfig(seed=902)),tmp_path)
    import rf_link_lab.simulation as sim
    monkeypatch.setattr(sim,'run_simulation',lambda *a,**k: (_ for _ in ()).throw(AssertionError('regenerated')))
    result=decode_recording(path)
    assert result.metrics['packets_ok']==12
    assert result.metrics['ber'] is None and result.metrics['evm_pct'] is None
    benchmark=decode_recording(path,use_saved_reference=True)
    assert benchmark.metrics['ber']==0 and benchmark.metrics['reference_available']


def test_raw_sidecar_integrity_and_provenance(tmp_path):
    wave=generate_waveform([Telemetry(12,400,True)],sample_rate=1000000,samples_per_symbol=20)
    iq=np.empty(wave.tx_iq.size*2,dtype='<i2');iq[0::2]=np.rint(wave.tx_iq.real*1500);iq[1::2]=np.rint(wave.tx_iq.imag*1500)
    path=tmp_path/'rx.sc16q11';path.write_bytes(iq.tobytes())
    sidecar={'source':'hardware_rx','requested':{'sample_rate':1000000},'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    path.with_suffix('.sc16q11.json').write_text(json.dumps(sidecar))
    result=decode_recording(path)
    assert result.metrics['packets_ok']==1 and result.metrics['ber'] is None
    assert result.metadata['original_origin']=='hardware_rx'
    assert result.metadata['sha256_verified']
    assert result.metadata['sample_rate']==200000
    path.write_bytes(path.read_bytes()+b'\0\0\0\0')
    with pytest.raises(ValueError,match='hash mismatch'):decode_recording(path)


def test_serial_origin_survives_save_and_redecode(tmp_path):
    wave=generate_waveform([Telemetry(1,20,False)])
    result=decode_iq(wave.tx_iq,{**wave.metadata,'provenance':'serial_waveform_software_loopback'},reference=wave)
    path=save_run(result,tmp_path)
    reprocessed=decode_recording(path,use_saved_reference=True)
    assert reprocessed.metadata['original_origin']=='serial_waveform_software_loopback'
    report=(tmp_path/'report.html').read_text()
    assert 'serial_waveform_software_loopback' in report and 'SYNTHETIC EXPERIMENT' not in report
    stored=load_run(path)
    assert stored.provenance.startswith('serial_waveform')
