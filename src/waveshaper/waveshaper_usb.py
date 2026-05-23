"""
waveshaper_usb.py
=================
Python 3 driver for the Finisar WaveShaper S-Series (e.g. 1000S) via USB,
wrapping wsapi.dll from WaveManager.

Based on the official Finisar wsapi.py (python3/ folder in WaveManager install).

Project layout
--------------
    waveshaper_usb.py
    dll/
        wsapi.dll
        ws_cheetah.dll
        ftd2xx.dll

Requirements
------------
    pip install numpy

Usage
-----
    from waveshaper_usb import WaveShaperUSB

    with WaveShaperUSB() as ws:
        print(ws.serial)
        ws.bandpass(centre_freq_thz=193.1, bandwidth_thz=0.2, port=1)
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import (
    c_byte,
    c_char_p,
    c_float,
    c_int,
    create_string_buffer,
    pointer,
)
from pathlib import Path
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DLL location
# ---------------------------------------------------------------------------
_DEFAULT_DLL_DIR = Path(__file__).parent / "dll"

if sys.platform == "win32" and _DEFAULT_DLL_DIR.exists():
    os.add_dll_directory(str(_DEFAULT_DLL_DIR))

# ---------------------------------------------------------------------------
# Profile type constants (from official wsapi.py)
# ---------------------------------------------------------------------------
PROFILE_TYPE_BLOCKALL = 1
PROFILE_TYPE_TRANSMIT = 2
PROFILE_TYPE_BANDPASS = 3
PROFILE_TYPE_BANDSTOP = 4
PROFILE_TYPE_GAUSSIAN = 5

_PROFILE_TYPE_MAP = {
    "blockall": PROFILE_TYPE_BLOCKALL,
    "transmit": PROFILE_TYPE_TRANSMIT,
    "bandpass": PROFILE_TYPE_BANDPASS,
    "bandstop": PROFILE_TYPE_BANDSTOP,
    "gaussian": PROFILE_TYPE_GAUSSIAN,
}

ProfileType = Literal["blockall", "transmit", "bandpass", "bandstop", "gaussian"]

# Module-level DLL singleton
_wsapi: ctypes.CDLL | None = None


def _load_dll(dll_dir: Path | None = None) -> ctypes.CDLL:
    global _wsapi
    if _wsapi is not None:
        return _wsapi

    search_dir = dll_dir or _DEFAULT_DLL_DIR

    if sys.platform == "win32":
        os.add_dll_directory(str(search_dir))

    for name in ("wsapi.dll", "wstestapi.dll", "libwsapi.so", "libwstestapi.so"):
        dll_path = search_dir / name
        if not dll_path.exists():
            continue
        try:
            _wsapi = ctypes.cdll.LoadLibrary(str(dll_path))
            logger.info("Loaded WaveShaper DLL: %s", dll_path)

            # ws_get_result_description and ws_get_version return char* directly
            _wsapi.ws_get_result_description.restype = c_char_p
            _wsapi.ws_get_version.restype = c_char_p

            return _wsapi
        except OSError as e:
            raise OSError(
                f"Could not load {dll_path}\nReason: {e}\n"
                "Ensure you are using 32-bit Python (the DLLs are 32-bit)."
            ) from e

    present = [f.name for f in search_dir.iterdir()] if search_dir.exists() else []
    raise FileNotFoundError(
        f"No WaveShaper DLL found in {search_dir}\n" f"Files present: {present}"
    )


def _check(rc: int | tuple, fn_name: str) -> None:
    """Raise on non-zero return code with human-readable description."""
    code = rc[0] if isinstance(rc, tuple) else rc
    if code == 0:
        return
    lib = _wsapi
    desc = ""
    if lib is not None:
        try:
            result = lib.ws_get_result_description(code)
            desc = result.decode(errors="replace") if result else ""
        except Exception:
            pass
    raise RuntimeError(
        f"WaveShaper API error in {fn_name}(): rc={code}"
        + (f" — {desc}" if desc else "")
    )


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class WaveShaperUSB:
    """Driver for the Finisar WaveShaper S-Series (1000S) via USB.

    Parameters
    ----------
    dll_dir:
        Folder containing wsapi.dll, ws_cheetah.dll, ftd2xx.dll.
        Defaults to ``dll/`` next to this script.
    wsconfig:
        WaveShaper config string passed to ws_create_waveshaper.
        Empty string = auto (correct for 1000S).
    device_name:
        Device serial number. Pass ``''`` to auto-discover.

    Examples
    --------
    ::
        with WaveShaperUSB() as ws:
            print(ws.serial)
            ws.bandpass(centre_freq_thz=193.1, bandwidth_thz=0.2, port=1)
    """

    # Default wsconfig path for the 1000S
    # Looks for wsconfig/SN91_1000S.wsconfig next to this script.
    WSCONFIG_1000S = str(Path(__file__).parent / "wsconfig" / "SN91_1000S.wsconfig")

    def __init__(
        self,
        dll_dir: str | Path | None = None,
        wsconfig: str = WSCONFIG_1000S,
        device_name: str = "",
    ) -> None:
        self._dll_dir = Path(dll_dir) if dll_dir else _DEFAULT_DLL_DIR
        self._wsconfig = wsconfig
        self._device_name_hint = device_name
        self._name: str | None = None
        self._lib: ctypes.CDLL | None = None

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    def open(self) -> None:
        """Load DLL, discover device, and open connection.

        Correct call sequence (from ws_api.h + empirical testing):
            1. ws_create_waveshaper_fromsno(name, serial)
            2. ws_open_waveshaper(name)
            3. ws_load_config(name, wsconfig_path)
        """
        self._lib = _load_dll(self._dll_dir)

        # Resolve serial number
        if self._device_name_hint:
            serial = self._device_name_hint
        else:
            devices = WaveShaperUSB.list_devices(self._dll_dir)
            if not devices:
                raise RuntimeError("No WaveShaper devices found. Check USB connection.")
            serial = devices[0]
            logger.info("Auto-discovered device: %s", serial)

        # 1. Create handle from serial number
        buf = create_string_buffer(b"myws", 64)
        rc = self._lib.ws_create_waveshaper_fromsno(buf, serial.encode("utf-8"))
        _check(rc, "ws_create_waveshaper_fromsno")
        self._name = buf.raw.decode("utf-8").strip("\x00")
        logger.info("WaveShaper handle: %s", self._name)

        # 2. Open USB connection
        rc = self._lib.ws_open_waveshaper(self._name.encode("utf-8"))
        _check(rc, "ws_open_waveshaper")

        # 3. Load device config (required before any profile loading)
        rc = self._lib.ws_load_config(
            self._name.encode("utf-8"), self._wsconfig.encode("utf-8")
        )
        _check(rc, "ws_load_config")
        logger.info("WaveShaper ready.")

    def close(self) -> None:
        """Close USB connection and release handle."""
        if self._lib is None or self._name is None:
            return
        rc = self._lib.ws_close_waveshaper(self._name.encode("utf-8"))
        if rc != 0:
            logger.warning("ws_close_waveshaper returned %d", rc)
        rc = self._lib.ws_delete_waveshaper(self._name.encode("utf-8"))
        if rc != 0:
            logger.warning("ws_delete_waveshaper returned %d", rc)
        self._name = None

    def __enter__(self) -> "WaveShaperUSB":
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Device info
    # ------------------------------------------------------------------ #

    @property
    def serial(self) -> str:
        """Device serial number."""
        self._require_open()
        buf = create_string_buffer(256)
        rc = self._lib.ws_read_sno(self._name.encode("utf-8"), buf, 256)
        if rc == 0:
            return buf.raw.strip(b"\x00").decode()
        # Fallback: return the handle name
        return self._name

    @property
    def version(self) -> str:
        """DLL version string."""
        result = self._lib.ws_get_version()
        return result.decode(errors="replace") if result else ""

    @property
    def frequency_range_thz(self) -> tuple[float, float]:
        """(start_freq_thz, stop_freq_thz). Falls back to C-band if unsupported."""
        self._require_open()
        f1, f2 = c_float(0.0), c_float(0.0)
        rc = self._lib.ws_get_frequencyrange(
            self._name.encode("utf-8"), pointer(f1), pointer(f2)
        )
        if rc == 0:
            return float(f1.value), float(f2.value)
        logger.warning(
            "ws_get_frequencyrange unsupported (rc=%d); returning C-band defaults.", rc
        )
        return 191.7, 194.1  # C-band defaults for 1000S

    @property
    def port_count(self) -> int:
        """Number of output ports."""
        self._require_open()
        i = c_int(0)
        rc = self._lib.ws_get_portcount(self._name.encode("utf-8"), pointer(i))
        return i.value if rc == 0 else 1  # 1000S is single port

    @staticmethod
    def list_devices(dll_dir: str | Path | None = None) -> list[str]:
        """Return list of connected WaveShaper device names."""
        lib = _load_dll(Path(dll_dir) if dll_dir else None)
        buf = create_string_buffer(1024)
        rc = lib.ws_list_devices(buf, 1024)
        if rc != 0:
            return []
        raw = buf.raw.decode("utf-8", errors="replace").strip("\x00").strip()
        return [d.rstrip(";").strip() for d in raw.splitlines() if d.strip()]

    @staticmethod
    def get_api_version(dll_dir: str | Path | None = None) -> str:
        """Return the wsapi.dll version string."""
        lib = _load_dll(Path(dll_dir) if dll_dir else None)
        result = lib.ws_get_version()
        return result.decode(errors="replace") if result else ""

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
        self._require_open()
        if profile_type not in _PROFILE_TYPE_MAP:
            raise ValueError(f"profile_type must be one of {set(_PROFILE_TYPE_MAP)}")

        filtertype = _PROFILE_TYPE_MAP[profile_type]
        rc = self._lib.ws_load_predefinedprofile(
            self._name.encode("utf-8"),
            filtertype,  # int, not string
            c_float.from_param(centre_freq_thz),
            c_float.from_param(bandwidth_thz),
            c_float.from_param(attenuation_db),
            port,
        )
        _check(rc, "ws_load_predefinedprofile")

    def block_all(self) -> None:
        """Block all wavelengths."""
        self.load_predefined_profile("blockall", 193.1, 10.0)

    def transmit_all(self) -> None:
        """Pass all wavelengths at 0 dB attenuation."""
        self.load_predefined_profile("transmit", 193.1, 10.0)

    def bandpass(
        self,
        centre_freq_thz: float,
        bandwidth_thz: float,
        attenuation_db: float = 0.0,
        port: int = 1,
    ) -> None:
        """Flat-top bandpass filter."""
        self.load_predefined_profile(
            "bandpass", centre_freq_thz, bandwidth_thz, attenuation_db, port
        )

    def gaussian_filter(
        self,
        centre_freq_thz: float,
        bandwidth_thz: float,
        attenuation_db: float = 0.0,
        port: int = 1,
    ) -> None:
        """Gaussian-shaped bandpass filter."""
        self.load_predefined_profile(
            "gaussian", centre_freq_thz, bandwidth_thz, attenuation_db, port
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

        Serialises arrays into WSP format and calls ws_load_profile.

        Parameters
        ----------
        freq_thz:
            Frequency axis in THz, strictly monotonically increasing.
        attenuation_db:
            Attenuation at each point in dB (≥ 0).
        phase_rad:
            Phase at each point in radians. Defaults to zeros.
        port:
            Output port(s) — scalar or array matching freq_thz length.
        """
        self._require_open()

        freq_thz = np.asarray(freq_thz, dtype=float)
        attenuation_db = np.asarray(attenuation_db, dtype=float)
        phase_rad = (
            np.zeros_like(freq_thz)
            if phase_rad is None
            else np.asarray(phase_rad, dtype=float)
        )
        port_arr = (
            np.full(len(freq_thz), int(port), dtype=int)
            if np.isscalar(port)
            else np.asarray(port, dtype=int)
        )

        n = len(freq_thz)
        if not (len(attenuation_db) == len(phase_rad) == len(port_arr) == n):
            raise ValueError("All arrays must have the same length.")
        if np.any(np.diff(freq_thz) <= 0):
            raise ValueError("freq_thz must be strictly monotonically increasing.")
        if np.any(attenuation_db < 0):
            raise ValueError("attenuation_db must be ≥ 0 dB.")

        wsp = "\n".join(
            f"{f:.6f}\t{a:.4f}\t{p:.6f}\t{prt}"
            for f, a, p, prt in zip(freq_thz, attenuation_db, phase_rad, port_arr)
        )

        # Official signature: ws_load_profile(name, wsptext) — 2 args only
        rc = self._lib.ws_load_profile(
            self._name.encode("utf-8"),
            wsp.encode("utf-8"),
        )
        _check(rc, "ws_load_profile")
        logger.debug("Loaded arbitrary profile (%d points).", n)

    # ------------------------------------------------------------------ #
    # Utilities
    # ------------------------------------------------------------------ #

    @staticmethod
    def wavelength_to_freq_thz(wavelength_nm: float) -> float:
        """Convert wavelength (nm) to frequency (THz)."""
        return 2.99792458e17 / wavelength_nm / 1e12

    @staticmethod
    def freq_thz_to_wavelength_nm(freq_thz: float) -> float:
        """Convert frequency (THz) to wavelength (nm)."""
        return 2.99792458e17 / (freq_thz * 1e12)

    def _require_open(self) -> None:
        if self._lib is None or self._name is None:
            raise RuntimeError("Not connected. Call open() or use as context manager.")

    def __repr__(self) -> str:
        name = self._name if self._name else "disconnected"
        return f"WaveShaperUSB(device={name!r})"
