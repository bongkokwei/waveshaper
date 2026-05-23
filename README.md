# waveshaper

Python driver for the **Finisar WaveShaper 1000S** via USB.

Supports both 32-bit and **64-bit** Python environments via a transparent
32-bit subprocess bridge.

---

## Requirements

| Component | Requirement |
|---|---|
| Calling environment | Python 3.9+, any bitness |
| Subprocess (server) | **32-bit** Python 3.9+ with `numpy` |
| DLLs | `wsapi.dll`, `ws_cheetah.dll`, `ftd2xx.dll` from WaveManager |

Place the DLLs in `waveshaper/dll/`.

---

## Installation

```bash
pip install -e .
```

> Install `numpy` in the **32-bit** interpreter too:
> ```
> C:\Python39-32\python.exe -m pip install numpy
> ```

---

## Usage

```python
from waveshaper import WaveShaperClient

with WaveShaperClient() as ws:
    print(ws.serial)                              # device serial number
    ws.bandpass(centre_freq_thz=193.1,
                bandwidth_thz=0.2, port=1)        # flat-top bandpass

    ws.gaussian_filter(centre_freq_thz=193.1,
                       bandwidth_thz=0.5)          # Gaussian shape

    ws.block_all()                                # block everything
    ws.transmit_all()                             # pass everything
```

### Arbitrary profile

```python
import numpy as np
from waveshaper import WaveShaperClient

freq = np.linspace(191.7, 194.1, 2000)          # THz
attn = np.where((freq > 192.9) & (freq < 193.3), 0.0, 50.0)  # dB

with WaveShaperClient() as ws:
    ws.load_profile(freq_thz=freq, attenuation_db=attn)
```

### Custom 32-bit Python path

```python
ws = WaveShaperClient(python32=r'C:\Python311-32\python.exe')
```

---

## Package layout

```
waveshaper/
├── __init__.py          # public API
├── client.py            # WaveShaperClient (64-bit safe proxy)
├── ws_server.py         # 32-bit subprocess server (owns wsapi.dll)
├── waveshaper_usb.py    # direct DLL wrapper (32-bit only)
├── dll/
│   ├── wsapi.dll
│   ├── ws_cheetah.dll
│   └── ftd2xx.dll
└── wsconfig/
    └── SN91_1000S.wsconfig
pyproject.toml
README.md
```

---

## How it works

`wsapi.dll` is a **32-bit** DLL and cannot be loaded by a 64-bit Python
process.  `WaveShaperClient` works around this by spawning `ws_server.py`
under a 32-bit interpreter.  The two processes communicate via
newline-delimited JSON on stdin/stdout — no network sockets or extra
dependencies required.

```
64-bit Python (your code)
        │  JSON over stdin/stdout
        ▼
32-bit Python  ←→  wsapi.dll  ←→  WaveShaper 1000S (USB)
```