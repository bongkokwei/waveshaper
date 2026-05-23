"""
waveshaper
==========
Python driver for the Finisar WaveShaper 1000S.

Public API
----------
:class:`WaveShaperClient`
    64-bit-safe proxy.  Spawns a 32-bit subprocess that owns ``wsapi.dll``.
    Use this in any 64-bit environment (the normal case).

:class:`WaveShaperUSB`
    Direct DLL wrapper.  Only usable from a **32-bit** Python process.
    Exposed here for use inside ``ws_server.py`` and for testing on
    a 32-bit interpreter directly.

Typical usage
-------------
::
    from waveshaper import WaveShaperClient

    with WaveShaperClient() as ws:
        print(ws.serial)
        ws.bandpass(centre_freq_thz=193.1, bandwidth_thz=0.2, port=1)
"""

from .client import WaveShaperClient
from .waveshaper_usb import WaveShaperUSB

__all__ = ["WaveShaperClient", "WaveShaperUSB"]
__version__ = "0.1.0"
