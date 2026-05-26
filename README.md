# waveshaper

Python driver for the **Finisar WaveShaper 1000S** via USB.

Two modes of operation:

- **`WaveShaperUSB`** — direct DLL wrapper. Use with the **64-bit** DLLs
  from `WaveManager\bin\amd64\` and any 64-bit Python, or with the 32-bit
  DLLs from a 32-bit interpreter. Simplest option if 64-bit DLLs are
  available.
- **`WaveShaperClient`** — 64-bit-safe proxy that spawns a 32-bit
  subprocess to load the 32-bit DLLs. Only needed when 64-bit DLLs are
  not available.

---

## Prerequisites

Install [WaveManager](https://ii-vi.com/waveshaper/) from II-VI/Coherent.
This provides the vendor DLLs and generates a device-specific `.wsconfig`
calibration file on first connection.

Copy DLLs into `src/waveshaper/dll/`:

| DLL source | Python bitness | Files to copy |
|---|---|---|
| `WaveManager\bin\amd64\` | **64-bit** (recommended) | `wsapi.dll`, `ws_cheetah.dll`, `ftd2xx64.dll` |
| `WaveManager\bin\` | 32-bit | `wsapi.dll`, `ws_cheetah.dll`, `ftd2xx.dll` |

If using the **64-bit DLLs**, you can use `WaveShaperUSB` directly — no
subprocess bridge or 32-bit Python needed.

The `.wsconfig` file is auto-resolved at runtime from
`%APPDATA%\WaveManager\wsconfig\SN<serial>.wsconfig` — no need to copy it
manually.

---

## Requirements

| Mode | Requirement |
|---|---|
| `WaveShaperUSB` + 64-bit DLLs | Python 3.9+ (64-bit), `numpy` |
| `WaveShaperUSB` + 32-bit DLLs | Python 3.9+ (32-bit), `numpy` |
| `WaveShaperClient` (subprocess bridge) | 64-bit Python for your code + **32-bit** Python 3.9+ with `numpy` for the server |

---

## Installation

```bash
pip install -e .
```

> If using `WaveShaperClient`, install `numpy` in the **32-bit** interpreter too:
> ```
> C:\Python39-32\python.exe -m pip install numpy
> ```

---

## Usage

### Direct access (recommended with 64-bit DLLs)

```python
from waveshaper import WaveShaperUSB

with WaveShaperUSB() as ws:
    print(ws.serial)                              # device serial number
    ws.bandpass(centre_freq_thz=193.1,
                bandwidth_thz=0.2, port=1)        # flat-top bandpass
    ws.gaussian_filter(centre_freq_thz=193.1,
                       bandwidth_thz=0.5)          # Gaussian shape
    ws.block_all()                                # block everything
    ws.transmit_all()                             # pass everything
```

### Via subprocess bridge (32-bit DLLs only)

```python
from waveshaper import WaveShaperClient

with WaveShaperClient() as ws:
    ws.bandpass(centre_freq_thz=193.1, bandwidth_thz=0.2, port=1)
```

Custom 32-bit Python path:

```python
ws = WaveShaperClient(python32=r'C:\Python311-32\python.exe')
```

### Arbitrary profile

```python
import numpy as np
from waveshaper import WaveShaperUSB

freq = np.linspace(191.7, 194.1, 2000)          # THz
attn = np.where((freq > 192.9) & (freq < 193.3), 0.0, 50.0)  # dB

with WaveShaperUSB() as ws:
    ws.load_profile(freq_thz=freq, attenuation_db=attn)
```

---

## Package layout

```
waveshaper/
├── src/
│   └── waveshaper/
│       ├── __init__.py          # public API
│       ├── client.py            # WaveShaperClient (64-bit safe proxy)
│       ├── ws_server.py         # 32-bit subprocess server (owns wsapi.dll)
│       ├── waveshaper_usb.py    # direct DLL wrapper
│       └── dll/                 # wsapi.dll, ws_cheetah.dll, ftd2xx.dll
├── tests/
│   └── test_client.py
├── pyproject.toml
└── README.md
```

---

## How it works

`WaveShaperUSB` wraps `wsapi.dll` via `ctypes`. If you have the **64-bit
DLLs** (from `WaveManager\bin\amd64\`), this works directly from any
64-bit Python — no subprocess needed.

If only the **32-bit DLLs** are available, `WaveShaperClient` works around
the bitness mismatch by spawning `ws_server.py` under a 32-bit interpreter.
The two processes communicate via newline-delimited JSON on stdin/stdout —
no network sockets or extra dependencies required.

```
64-bit Python (your code)
        │  JSON over stdin/stdout
        ▼
32-bit Python  ←→  wsapi.dll  ←→  WaveShaper 1000S (USB)
```

### Connection sequence

The driver uses the following call order (confirmed empirically — May 2025):

1. `ws_create_waveshaper(name, wsconfig_path)` — allocate handle and
   initialise the LCOS hardware state
2. `ws_open_waveshaper(name)` — open USB connection

The `.wsconfig` path is auto-resolved from `%APPDATA%\WaveManager\wsconfig\`
using the device serial number, or can be passed explicitly.

> **Note:** An earlier version used `ws_create_waveshaper_fromsno` +
> `ws_load_config`. This writes the DLL's internal model correctly but does
> not physically commit the profile to the LCOS — use `ws_create_waveshaper`
> instead.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `OSError: Could not load wsapi.dll` | DLL bitness doesn't match Python — use 64-bit DLLs with 64-bit Python, or 32-bit DLLs with 32-bit Python |
| `No WaveShaper devices found` | USB cable disconnected or WaveManager driver not installed |
| `wsconfig not found` | WaveManager hasn't been run yet for this device — open it once in the GUI to generate the config |
| Profile loads but measures wrong | WaveManager background service may still own the device — check Task Manager for lingering `WaveManager` processes and kill them |
| `ws_load_config` error | `.wsconfig` path wrong or file missing |

---

## Licence

Internal lab use — COMBS group, Monash University.