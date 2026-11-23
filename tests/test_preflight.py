from rf_link_lab.preflight import doctor,self_test
from rf_link_lab import __version__


def test_doctor_never_opens_hardware(monkeypatch):
    import rf_link_lab.hardware as hardware
    monkeypatch.setattr(hardware,'probe_bladerf',lambda: (_ for _ in ()).throw(AssertionError('opened device')))
    result=doctor()
    assert result['device_opened'] is False
    assert result['rf_transmission_enabled'] is False
    assert result['application_version']==__version__


def test_software_self_test_writes_report(tmp_path):
    output=tmp_path/'self-test.json'
    result=self_test(output)
    assert result['passed']
    assert result['checks']['missing_reference_ber_unavailable']
    assert output.is_file()
