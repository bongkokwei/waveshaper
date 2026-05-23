"""
tests/test_client.py
====================
Unit tests for WaveShaperClient.

All tests mock the subprocess so no hardware or 32-bit Python is needed.
The mock simulates ws_server.py's stdin/stdout JSON protocol.

Run with:
    pytest tests/test_client.py -v
"""

from __future__ import annotations

import io
import json
import subprocess
import threading
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from waveshaper.client import WaveShaperClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _encode(obj: dict) -> str:
    return json.dumps(obj) + "\n"


class _FakeServer:
    """Simulates ws_server.py's stdin/stdout protocol.

    Construct with a sequence of response dicts.  The first response is
    always sent immediately (startup handshake); subsequent responses are
    returned in order for each _call().

    Parameters
    ----------
    responses:
        Ordered list of dicts the fake server will write to stdout.
        The first entry is the startup acknowledgement.
    """

    def __init__(self, responses: list[dict]) -> None:
        # Build stdout content: all responses concatenated
        stdout_text = "".join(_encode(r) for r in responses)
        self.stdout = io.StringIO(stdout_text)
        self.stdin = io.StringIO()
        self.stderr = io.StringIO("")
        self.pid = 99999
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._returncode = 0
        return 0

    def kill(self) -> None:
        self._returncode = -9


def _make_client(
    responses: list[dict], **kwargs
) -> tuple[WaveShaperClient, _FakeServer]:
    """Return a (client, fake_server) pair with subprocess patched out."""
    server = _FakeServer(responses)

    with patch("subprocess.Popen", return_value=server):
        client = WaveShaperClient(python32="fake_python32.exe", **kwargs)
        client.open()

    return client, server


# Shorthand ready response
_READY = {"ok": True, "value": "ready"}
_OK = {"ok": True}


# ---------------------------------------------------------------------------
# open() / close()
# ---------------------------------------------------------------------------


class TestConnection:

    def test_open_succeeds_on_ready_signal(self):
        client, _ = _make_client([_READY, _OK])  # _OK for the close cmd
        assert client._proc is not None

    def test_open_raises_on_server_error(self):
        server = _FakeServer([{"ok": False, "error": "DLL not found"}])
        with patch("subprocess.Popen", return_value=server):
            client = WaveShaperClient(python32="fake.exe")
            with pytest.raises(RuntimeError, match="DLL not found"):
                client.open()

    def test_open_raises_on_timeout(self):
        # Server that never responds
        server = _FakeServer([])
        server.stdout = io.StringIO("")  # EOF immediately → thread reads ''

        with patch("subprocess.Popen", return_value=server):
            client = WaveShaperClient(python32="fake.exe", startup_timeout_s=0.1)
            with pytest.raises((RuntimeError, TimeoutError)):
                client.open()

    def test_close_sends_close_cmd_and_nones_proc(self):
        client, server = _make_client([_READY, _OK])
        client.close()
        assert client._proc is None

    def test_context_manager(self):
        server = _FakeServer([_READY, _OK])
        with patch("subprocess.Popen", return_value=server):
            with WaveShaperClient(python32="fake.exe") as ws:
                assert ws._proc is not None
        assert ws._proc is None

    def test_repr_disconnected(self):
        ws = WaveShaperClient()
        assert "disconnected" in repr(ws)

    def test_repr_connected(self):
        client, _ = _make_client([_READY, _OK])
        assert "pid=99999" in repr(client)


# ---------------------------------------------------------------------------
# _call() error handling
# ---------------------------------------------------------------------------


class TestCall:

    def test_raises_when_not_connected(self):
        ws = WaveShaperClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            ws._call("serial")

    def test_raises_on_server_error_response(self):
        client, _ = _make_client(
            [
                _READY,
                {"ok": False, "error": "hardware fault"},
                _OK,
            ]
        )
        with pytest.raises(RuntimeError, match="hardware fault"):
            client._call("bandpass")
        client.close()

    def test_raises_when_proc_exits_unexpectedly(self):
        client, server = _make_client([_READY, _OK])
        # Exhaust stdout so readline returns ''
        server.stdout = io.StringIO("")
        server._returncode = 1
        with pytest.raises(RuntimeError, match="closed unexpectedly"):
            client._call("serial")


# ---------------------------------------------------------------------------
# Device info properties
# ---------------------------------------------------------------------------


class TestDeviceInfo:

    def test_serial(self):
        client, _ = _make_client([_READY, {"ok": True, "value": "SN12345"}, _OK])
        assert client.serial == "SN12345"
        client.close()

    def test_version(self):
        client, _ = _make_client([_READY, {"ok": True, "value": "3.7.1"}, _OK])
        assert client.version == "3.7.1"
        client.close()

    def test_frequency_range_thz(self):
        client, _ = _make_client(
            [
                _READY,
                {"ok": True, "value": [191.7, 194.1]},
                _OK,
            ]
        )
        lo, hi = client.frequency_range_thz
        assert lo == pytest.approx(191.7)
        assert hi == pytest.approx(194.1)
        client.close()

    def test_port_count(self):
        client, _ = _make_client([_READY, {"ok": True, "value": 1}, _OK])
        assert client.port_count == 1
        client.close()


# ---------------------------------------------------------------------------
# Predefined profiles — verify correct JSON is sent
# ---------------------------------------------------------------------------


class TestPredefinedProfiles:
    """Check that the right cmd and kwargs reach the server."""

    def _sent_requests(self, server: _FakeServer) -> list[dict]:
        return [
            json.loads(l) for l in server.stdin.getvalue().splitlines() if l.strip()
        ]

    def test_bandpass_sends_correct_cmd(self):
        client, server = _make_client([_READY, _OK, _OK])
        client.bandpass(
            centre_freq_thz=193.1, bandwidth_thz=0.2, attenuation_db=3.0, port=1
        )
        client.close()

        reqs = self._sent_requests(server)
        bp = reqs[0]
        assert bp["cmd"] == "bandpass"
        assert bp["kwargs"]["centre_freq_thz"] == pytest.approx(193.1)
        assert bp["kwargs"]["bandwidth_thz"] == pytest.approx(0.2)
        assert bp["kwargs"]["attenuation_db"] == pytest.approx(3.0)
        assert bp["kwargs"]["port"] == 1

    def test_gaussian_filter_sends_correct_cmd(self):
        client, server = _make_client([_READY, _OK, _OK])
        client.gaussian_filter(centre_freq_thz=193.0, bandwidth_thz=0.5)
        client.close()

        reqs = self._sent_requests(server)
        assert reqs[0]["cmd"] == "gaussian_filter"
        assert reqs[0]["kwargs"]["centre_freq_thz"] == pytest.approx(193.0)

    def test_block_all(self):
        client, server = _make_client([_READY, _OK, _OK])
        client.block_all()
        client.close()
        reqs = self._sent_requests(server)
        assert reqs[0]["cmd"] == "block_all"

    def test_transmit_all(self):
        client, server = _make_client([_READY, _OK, _OK])
        client.transmit_all()
        client.close()
        reqs = self._sent_requests(server)
        assert reqs[0]["cmd"] == "transmit_all"

    def test_load_predefined_profile(self):
        client, server = _make_client([_READY, _OK, _OK])
        client.load_predefined_profile(
            "bandstop", 193.1, 0.3, attenuation_db=10.0, port=1
        )
        client.close()
        reqs = self._sent_requests(server)
        assert reqs[0]["cmd"] == "load_predefined_profile"
        assert reqs[0]["kwargs"]["profile_type"] == "bandstop"


# ---------------------------------------------------------------------------
# load_profile — array serialisation
# ---------------------------------------------------------------------------


class TestLoadProfile:

    def _sent_requests(self, server: _FakeServer) -> list[dict]:
        return [
            json.loads(l) for l in server.stdin.getvalue().splitlines() if l.strip()
        ]

    def test_arrays_serialised_as_lists(self):
        freq = np.linspace(191.7, 194.1, 10)
        attn = np.zeros(10)

        client, server = _make_client([_READY, _OK, _OK])
        client.load_profile(freq_thz=freq, attenuation_db=attn)
        client.close()

        reqs = self._sent_requests(server)
        kw = reqs[0]["kwargs"]
        assert isinstance(kw["freq_thz"], list)
        assert isinstance(kw["attenuation_db"], list)
        assert kw["phase_rad"] is None
        assert len(kw["freq_thz"]) == 10
        assert kw["freq_thz"][0] == pytest.approx(191.7)

    def test_phase_rad_serialised_when_provided(self):
        freq = np.linspace(191.7, 194.1, 5)
        attn = np.zeros(5)
        phase = np.linspace(0, np.pi, 5)

        client, server = _make_client([_READY, _OK, _OK])
        client.load_profile(freq_thz=freq, attenuation_db=attn, phase_rad=phase)
        client.close()

        reqs = self._sent_requests(server)
        kw = reqs[0]["kwargs"]
        assert kw["phase_rad"] is not None
        assert len(kw["phase_rad"]) == 5
        assert kw["phase_rad"][-1] == pytest.approx(np.pi)

    def test_scalar_port_sent_as_int(self):
        freq = np.linspace(191.7, 194.1, 5)
        attn = np.zeros(5)

        client, server = _make_client([_READY, _OK, _OK])
        client.load_profile(freq_thz=freq, attenuation_db=attn, port=1)
        client.close()

        reqs = self._sent_requests(server)
        assert reqs[0]["kwargs"]["port"] == 1


# ---------------------------------------------------------------------------
# Utility static methods — no hardware needed
# ---------------------------------------------------------------------------


class TestUtilities:

    def test_wavelength_to_freq_thz_1550nm(self):
        freq = WaveShaperClient.wavelength_to_freq_thz(1550.0)
        assert freq == pytest.approx(193.414, rel=1e-4)

    def test_freq_thz_to_wavelength_nm_roundtrip(self):
        wl = 1310.0
        freq = WaveShaperClient.wavelength_to_freq_thz(wl)
        wl2 = WaveShaperClient.freq_thz_to_wavelength_nm(freq)
        assert wl2 == pytest.approx(wl, rel=1e-9)

    def test_freq_thz_to_wavelength_nm_193thz(self):
        wl = WaveShaperClient.freq_thz_to_wavelength_nm(193.1)
        assert wl == pytest.approx(1552.52, rel=1e-3)
