import pytest

from rf_link_lab.protocol import (FRAME_SIZE, Telemetry, TelemetryParser,
                                  decode_telemetry, encode_telemetry)


def test_wire_round_trip_and_size():
    message = Telemetry(0xFFFFFFFF, 1234567, True)
    encoded = encode_telemetry(message)
    assert len(encoded) == FRAME_SIZE
    assert encoded[:4] == b"\xb5\x62\x01\x09"
    assert decode_telemetry(encoded) == message


def test_crc_rejects_corruption():
    data = bytearray(encode_telemetry(Telemetry(17, 1800, False)))
    data[6] ^= 4
    with pytest.raises(ValueError, match="CRC"):
        decode_telemetry(bytes(data))


def test_stream_parser_recovers_after_truncation_junk_and_crc():
    first, second = Telemetry(1, 100, True), Telemetry(2, 200, False)
    damaged = bytearray(encode_telemetry(first))
    damaged[-1] ^= 1
    stream = (b"junk\xb5" + encode_telemetry(first)[:7] + bytes(damaged)
              + encode_telemetry(first) + encode_telemetry(second))
    parser = TelemetryParser()
    observed = []
    for start in range(0, len(stream), 3):
        observed.extend(parser.feed(stream[start:start + 3]))
    assert observed == [first, second]
    assert parser.rejected_frames >= 2

