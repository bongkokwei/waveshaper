"""
client.py
=========
64-bit-safe proxy for the Finisar WaveShaper 1000S.

Spawns ``ws_server.py`` under a 32-bit Python interpreter (which can load
wsapi.dll) and communicates via newline-delimited JSON on stdin/stdout.

Usage
-----
::
    from waveshaper import WaveShaperClient

    with WaveShaperClient() as ws:
        print(ws.serial)
        ws.bandpass(centre_freq_thz=193.1, bandwidth_thz=0.2, port=1)

Configuration
-------------
If your 32-bit Python is not at the default path, pass ``python32`` explicitly:

::
    ws = WaveShaperClient(python32=r'C:\\Python39-32\\python.exe')
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# Path to ws_server.py, assumed to sit alongside this file inside the package
_SERVER_SCRIPT = Path(__file__).parent / "ws_server.py"

# Default 32-bit Python executable path (Windows)
_DEFAULT_PYTHON32 = r"C:\Python39-32\python.exe"

ProfileType = Literal["blockall", "transmit", "bandpass", "bandstop", "gaussian"]


class WaveShaperClient:
    """64-bit-safe proxy for the Finisar WaveShaper 1000S.

    Spawns a 32-bit subprocess that owns wsapi.dll, then communicates
    via newline-delimited JSON on stdin/stdout.

    Parameters
    ----------
    python32:
        Path to a 32-bit Python executable.  The interpreter must have
        ``numpy`` installed and be able to import ``waveshaper_usb``.
    dll_dir:
        Passed through to ``ws_server.py`` (folder containing wsapi.dll).
        ``None`` → default ``dll/`` subfolder inside the package.
    wsconfig:
        Path to the ``.wsconfig`` file.  ``None`` → default 1000S config.
    device:
        Device serial number.  Empty string → auto-discover first device.
    startup_timeout_s:
        Seconds to wait for the subprocess to signal readiness.
    """

    def __init__(
        self,
        python32: str | Path = _DEFAULT_PYTHON32,
        dll_dir: str | Path | None = None,
        wsconfig: str | None = None,
        device: str = "",
        startup_timeout_s: float = 15.0,
    ) -> None:
        self._python32 = str(python32)
        self._dll_dir = str(dll_dir) if dll_dir else None
        self._wsconfig = str(wsconfig) if wsconfig else None
        self._device = device
        self._startup_timeout_s = startup_timeout_s
        self._proc: subprocess.Popen | None = None

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def open(self) -> None:
        """Spawn the 32-bit server subprocess and wait for readiness signal."""
        cmd = [self._python32, str(_SERVER_SCRIPT)]
        if self._dll_dir:
            cmd += ["--dll-dir", self._dll_dir]
        if self._wsconfig:
            cmd += ["--wsconfig", self._wsconfig]
        if self._device:
            cmd += ["--device", self._device]

        logger.info("Spawning WaveShaper server: %s", " ".join(cmd))

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # server logs go to stderr; we capture separately
            text=True,
            bufsize=1,  # line-buffered
        )

        # Wait for startup acknowledgement with a timeout
        import select
        import threading

        startup_resp: dict = {}
        error_event = threading.Event()

        def _read_startup() -> None:
            line = self._proc.stdout.readline()
            try:
                startup_resp.update(json.loads(line))
            except Exception:
                startup_resp["ok"] = False
                startup_resp["error"] = f"Bad startup response: {line!r}"
            error_event.set()

        t = threading.Thread(target=_read_startup, daemon=True)
        t.start()
        t.join(timeout=self._startup_timeout_s)

        if not error_event.is_set():
            self._proc.kill()
            raise TimeoutError(
                f"WaveShaper server did not respond within {self._startup_timeout_s} s. "
                "Check that the 32-bit Python path is correct and wsapi.dll is present."
            )

        if not startup_resp.get("ok"):
            stderr_text = self._proc.stderr.read()
            raise RuntimeError(
                f'WaveShaper server failed to start: {startup_resp.get("error")}\n'
                f"Server stderr:\n{stderr_text}"
            )

        logger.info("WaveShaper server ready (pid=%d).", self._proc.pid)

    def close(self) -> None:
        """Send close command and terminate the subprocess."""
        if self._proc is None:
            return
        try:
            self._call("close")
        except Exception:
            pass
        try:
            self._proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.warning("Server did not exit cleanly; terminating.")
            self._proc.kill()
        self._proc = None

    def __enter__(self) -> "WaveShaperClient":
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # IPC
    # ------------------------------------------------------------------ #

    def _call(self, cmd: str, **kwargs) -> object:
        """Send one JSON request and return the ``value`` field of the response."""
        if self._proc is None:
            raise RuntimeError("Not connected. Call open() or use as context manager.")
        if self._proc.poll() is not None:
            raise RuntimeError(
                f"WaveShaper server closed unexpectedly (rc={self._proc.poll()})."
            )

        msg = json.dumps({"cmd": cmd, "kwargs": kwargs}) + "\n"
        self._proc.stdin.write(msg)
        self._proc.stdin.flush()

        raw = self._proc.stdout.readline()
        if not raw:
            stderr_text = self._proc.stderr.read()
            raise RuntimeError(
                f"WaveShaper server closed unexpectedly.\nServer stderr:\n{stderr_text}"
            )

        resp = json.loads(raw)
        if not resp.get("ok"):
            raise RuntimeError(f'WaveShaper error ({cmd}): {resp.get("error")}')

        return resp.get("value")

    # ------------------------------------------------------------------ #
    # Device info
    # ------------------------------------------------------------------ #

    @property
    def serial(self) -> str:
        """Device serial number."""
        return str(self._call("serial"))

    @property
    def version(self) -> str:
        """DLL version string."""
        return str(self._call("version"))

    @property
    def frequency_range_thz(self) -> tuple[float, float]:
        """(start_freq_thz, stop_freq_thz)."""
        lo, hi = self._call("frequency_range_thz")
        return float(lo), float(hi)

    @property
    def port_count(self) -> int:
        """Number of output ports."""
        return int(self._call("port_count"))

    # ------------------------------------------------------------------ #
    # Predefined profiles
    # ------------------------------------------------------------------ #

    def load_predefined_profile(
        self,
        profile_type: ProfileType,
        centre_freq_thz: float,
        bandwidth_thz: float,
        attenuation_db: float = 0.0,
        port: int = 1,
    ) -> None:
        """Upload a predefined filter profile.

        Parameters
        ----------
        profile_type:
            ``'blockall'``, ``'transmit'``, ``'bandpass'``,
            ``'bandstop'``, or ``'gaussian'``.
        centre_freq_thz:
            Centre frequency in THz.
        bandwidth_thz:
            3 dB bandwidth in THz.
        attenuation_db:
            In-band attenuation in dB (0–30 dB).
        port:
            Output port number (1 for 1000S).
        """
        self._call(
            "load_predefined_profile",
            profile_type=profile_type,
            centre_freq_thz=centre_freq_thz,
            bandwidth_thz=bandwidth_thz,
            attenuation_db=attenuation_db,
            port=port,
        )

    def block_all(self) -> None:
        """Block all wavelengths."""
        self._call("block_all")

    def transmit_all(self) -> None:
        """Pass all wavelengths at 0 dB attenuation."""
        self._call("transmit_all")

    def bandpass(
        self,
        centre_freq_thz: float,
        bandwidth_thz: float,
        attenuation_db: float = 0.0,
        port: int = 1,
    ) -> None:
        """Flat-top bandpass filter."""
        self._call(
            "bandpass",
            centre_freq_thz=centre_freq_thz,
            bandwidth_thz=bandwidth_thz,
            attenuation_db=attenuation_db,
            port=port,
        )

    def gaussian_filter(
        self,
        centre_freq_thz: float,
        bandwidth_thz: float,
        attenuation_db: float = 0.0,
        port: int = 1,
    ) -> None:
        """Gaussian-shaped bandpass filter."""
        self._call(
            "gaussian_filter",
            centre_freq_thz=centre_freq_thz,
            bandwidth_thz=bandwidth_thz,
            attenuation_db=attenuation_db,
            port=port,
        )

    # ------------------------------------------------------------------ #
    # Arbitrary WSP profile
    # ------------------------------------------------------------------ #

    def load_profile(
        self,
        freq_thz: np.ndarray,
        attenuation_db: np.ndarray,
        phase_rad: np.ndarray | None = None,
        port: np.ndarray | int = 1,
    ) -> None:
        """Upload an arbitrary filter profile from numpy arrays.

        Arrays are serialised to lists for JSON transport and reconstructed
        inside the 32-bit server process.

        Parameters
        ----------
        freq_thz:
            Frequency axis in THz, strictly monotonically increasing.
        attenuation_db:
            Attenuation at each point in dB (≥ 0).
        phase_rad:
            Phase at each point in radians.  Defaults to zeros.
        port:
            Output port(s) — scalar or array matching ``freq_thz`` length.
        """
        freq_thz = np.asarray(freq_thz, dtype=float)
        attenuation_db = np.asarray(attenuation_db, dtype=float)

        self._call(
            "load_profile",
            freq_thz=freq_thz.tolist(),
            attenuation_db=attenuation_db.tolist(),
            phase_rad=phase_rad.tolist() if phase_rad is not None else None,
            port=int(port) if np.isscalar(port) else np.asarray(port).tolist(),
        )

    # ------------------------------------------------------------------ #
    # Utilities (pure Python — no subprocess needed)
    # ------------------------------------------------------------------ #

    @staticmethod
    def wavelength_to_freq_thz(wavelength_nm: float) -> float:
        """Convert wavelength (nm) to frequency (THz)."""
        return 2.99792458e17 / wavelength_nm / 1e12

    @staticmethod
    def freq_thz_to_wavelength_nm(freq_thz: float) -> float:
        """Convert frequency (THz) to wavelength (nm)."""
        return 2.99792458e17 / (freq_thz * 1e12)

    def __repr__(self) -> str:
        state = f"pid={self._proc.pid}" if self._proc else "disconnected"
        return f"WaveShaperClient({state})"
