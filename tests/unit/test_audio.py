import numpy as np
import pytest
import soundfile as sf

from callintel.audio.preprocess import preprocess_file
from callintel.audio.resample import StreamingResampler, resample
from callintel.audio.vad import compute_features, runs, speech_mask
from callintel.errors import CorruptAudioError, UnsupportedAudioError


def tone(freq, sr, seconds, amp=0.5):
    t = np.arange(int(sr * seconds)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def dominant_hz(x, sr):
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return np.fft.rfftfreq(len(x), 1 / sr)[np.argmax(spectrum)]


@pytest.mark.parametrize(("sr_in", "sr_out"), [(8000, 16000), (44100, 16000), (24000, 8000)])
def test_resample_preserves_frequency_amplitude_and_length(sr_in, sr_out):
    x = tone(1000, sr_in, 1.0)
    y = resample(x, sr_in, sr_out)
    assert len(y) == int(np.ceil(len(x) * sr_out / sr_in))
    assert abs(dominant_hz(y, sr_out) - 1000) < 5
    core = y[len(y) // 4: 3 * len(y) // 4]
    assert np.sqrt(np.mean(core**2)) == pytest.approx(0.5 / np.sqrt(2), rel=0.02)


def test_downsampling_removes_content_above_new_nyquist():
    y = resample(tone(6000, 48000, 1.0), 48000, 8000)  # 6 kHz > 4 kHz Nyquist
    assert np.sqrt(np.mean(y**2)) < 0.01


def test_streaming_equals_one_shot():
    rng = np.random.default_rng(1)
    x = rng.normal(0, 0.2, (8000 * 3, 2)).astype(np.float32)
    rs = StreamingResampler(8000, 16000, 2)
    blocks = [rs.push(x[i: i + 1234]) for i in range(0, len(x), 1234)] + [rs.flush()]
    np.testing.assert_allclose(np.concatenate(blocks), resample(x, 8000, 16000), atol=1e-5)


def test_vad_and_features_detect_silence_and_overlap():
    frame = 0.02
    a = np.r_[np.full(100, -20.0), np.full(1600, -70.0), np.full(100, -20.0)]
    b = np.r_[np.full(80, -70.0), np.full(40, -20.0), np.full(1680, -70.0)]  # overlaps a
    masks = [speech_mask(a), speech_mask(b)]
    f = compute_features(masks, ["agent", "customer"], frame)
    # b talks until frame 120, a resumes at 1700: (1700 - 120) * 20 ms of dead air
    assert f["longest_silence_s"] == pytest.approx(31.6, abs=0.05)
    assert len(f["silences"]) == 1
    assert len(f["overlap_events"]) == 1


def test_short_gaps_are_bridged():
    db = np.r_[np.full(50, -20.0), np.full(5, -70.0), np.full(50, -20.0)]
    assert runs(speech_mask(db)) == [(0, 105)]


def write(path, data, sr, **kw):
    sf.write(str(path), data, sr, **kw)
    return path


def test_preprocess_stereo_wav(tmp_path):
    x = np.stack([tone(300, 8000, 3), tone(500, 8000, 3)], axis=1)
    src = write(tmp_path / "in.wav", x, 8000, subtype="PCM_16")
    out = tmp_path / "out"
    out.mkdir()
    r = preprocess_file(src, out, channel_map=["agent", "customer"], min_duration_s=1,
                        max_duration_s=60)
    assert r.channels == 2 and r.duration_s == pytest.approx(3.0)
    for p in r.outputs:
        info = sf.info(str(p))
        assert info.samplerate == 16000 and info.channels == 1
    assert set(r.features["talk_time_s"]) == {"agent", "customer"}


def test_preprocess_rejects_garbage(tmp_path):
    src = tmp_path / "bad.wav"
    src.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 64)
    with pytest.raises(CorruptAudioError):
        preprocess_file(src, tmp_path, channel_map=["a", "c"], min_duration_s=1,
                        max_duration_s=60)


def test_preprocess_rejects_too_short(tmp_path):
    src = write(tmp_path / "short.wav", tone(300, 8000, 0.3), 8000)
    with pytest.raises(UnsupportedAudioError):
        preprocess_file(src, tmp_path, channel_map=["a", "c"], min_duration_s=1,
                        max_duration_s=60)
