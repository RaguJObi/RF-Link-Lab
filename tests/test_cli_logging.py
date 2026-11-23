"""Public command-line workflows preserve evidence without a physical board."""
import json
import threading

import pytest

from rf_link_lab.cli import main,record_uart
from rf_link_lab.serial_logging import verify_uart_recording


def test_cli_offline_demo_verification_and_uart_replay(tmp_path,capsys,monkeypatch):
    import serial
    monkeypatch.setattr(serial,'Serial',lambda **kw:pytest.fail('Offline command opened a device'))
    folder=tmp_path/'example'
    assert main(['bench-demo','--output',str(folder)])==0
    assert json.loads(capsys.readouterr().out)['manifest'].endswith('bench.json')
    assert main(['bench-verify',str(folder)])==0
    assert json.loads(capsys.readouterr().out)['status']=='verified'
    assert main(['uart-replay',str(folder/'uart')])==0
    assert json.loads(capsys.readouterr().out)['matches']
    with (folder/'uart/raw.bin').open('ab') as stream:stream.write(b'changed')
    with pytest.raises(SystemExit) as exc:main(['uart-replay',str(folder/'uart')])
    assert exc.value.code==2 and 'mismatch' in capsys.readouterr().err


def test_explicit_recording_retains_failed_open_and_returns_failure(tmp_path,capsys,monkeypatch):
    import serial
    calls=[]
    def fail(**settings):
        calls.append(settings);raise OSError('Selected endpoint permission denied')
    monkeypatch.setattr(serial,'Serial',fail)
    folder=tmp_path/'failed'
    assert main(['serial-record','--port','COM-TEST-ONLY','--output',str(folder),'--duration','.1'])==1
    result=json.loads(capsys.readouterr().out)
    assert not result['succeeded'] and 'permission denied' in result['error']
    assert len(calls)==1 and calls[0]['port']=='COM-TEST-ONLY'
    verification=verify_uart_recording(folder)
    assert verification['summary']['bytes_received']==0
    manifest=json.loads((folder/'uart.json').read_text())
    assert manifest['stream_error_count']==1
    assert manifest['stream_errors'][0]['kind']=='open_error'


@pytest.mark.parametrize('duration',[0,float('nan'),float('inf'),86401])
def test_invalid_recording_duration_creates_no_evidence_or_device(tmp_path,monkeypatch,duration):
    import serial
    monkeypatch.setattr(serial,'Serial',lambda **kw:pytest.fail('Invalid configuration opened serial'))
    with pytest.raises(ValueError,match='duration'):record_uart(tmp_path/'invalid','COM-TEST',duration=duration)
    assert not (tmp_path/'invalid').exists()


def test_keyboard_interrupt_finalizes_recording_and_returns_interrupted(tmp_path,monkeypatch):
    import serial
    import time
    class WaitingSerial:
        def __init__(self,**settings):self.stopped=threading.Event()
        def read(self,count):self.stopped.wait(.01);return b''
        def cancel_read(self):self.stopped.set()
        def close(self):self.stopped.set()
    monkeypatch.setattr(serial,'Serial',WaitingSerial)
    monkeypatch.setattr(time,'sleep',lambda duration:(_ for _ in ()).throw(KeyboardInterrupt()))
    result=record_uart(tmp_path/'interrupted','COM-TEST',duration=.1)
    assert result['interrupted'] and not result['succeeded']
    assert verify_uart_recording(tmp_path/'interrupted')['complete']
