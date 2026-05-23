"""
ws_server.py
============
32-bit subprocess server for WaveShaperUSB.

Run with a 32-bit Python interpreter — this process loads wsapi.dll directly.
Not intended to be imported; it is spawned by WaveShaperClient.

Usage (internal, via WaveShaperClient)
---------------------------------------
    C:\\Python39-32\\python.exe ws_server.py [--dll-dir path] [--wsconfig path] [--device serial]

Protocol
--------
Newline-delimited JSON on stdin/stdout.

Request  : {"cmd": "<command>", "kwargs": {...}}
Response : {"ok": true, "value": <optional>}
         | {"ok": false, "error": "<message>"}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure waveshaper_usb is importable when this script lives inside the package
sys.path.insert(0, str(Path(__file__).parent))
from waveshaper_usb import WaveShaperUSB  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [server] %(levelname)s %(message)s",
    stream=sys.stderr,  # keep stdout clean for JSON protocol
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------


def _dispatch(ws: WaveShaperUSB, req: dict) -> dict:
    """Execute one request and return a response dict."""
    cmd = req.get("cmd", "")
    kwargs = req.get("kwargs", {})

    if cmd == "bandpass":
        ws.bandpass(**kwargs)
        return {"ok": True}

    if cmd == "gaussian_filter":
        ws.gaussian_filter(**kwargs)
        return {"ok": True}

    if cmd == "block_all":
        ws.block_all()
        return {"ok": True}

    if cmd == "transmit_all":
        ws.transmit_all()
        return {"ok": True}

    if cmd == "load_predefined_profile":
        ws.load_predefined_profile(**kwargs)
        return {"ok": True}

    if cmd == "load_profile":
        import numpy as np

        # Arrays arrive as plain lists over JSON — convert back
        freq_thz = np.asarray(kwargs["freq_thz"])
        attenuation_db = np.asarray(kwargs["attenuation_db"])
        phase_rad = (
            np.asarray(kwargs["phase_rad"])
            if kwargs.get("phase_rad") is not None
            else None
        )
        port = kwargs.get("port", 1)
        ws.load_profile(freq_thz, attenuation_db, phase_rad, port)
        return {"ok": True}

    if cmd == "serial":
        return {"ok": True, "value": ws.serial}

    if cmd == "version":
        return {"ok": True, "value": ws.version}

    if cmd == "frequency_range_thz":
        lo, hi = ws.frequency_range_thz
        return {"ok": True, "value": [lo, hi]}

    if cmd == "port_count":
        return {"ok": True, "value": ws.port_count}

    if cmd == "close":
        return {"ok": True, "value": "__close__"}

    return {"ok": False, "error": f"Unknown command: {cmd!r}"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="WaveShaper 32-bit IPC server")
    parser.add_argument(
        "--dll-dir", default=None, help="Path to folder containing wsapi.dll"
    )
    parser.add_argument(
        "--wsconfig",
        default=WaveShaperUSB.WSCONFIG_1000S,
        help="Path to .wsconfig file",
    )
    parser.add_argument(
        "--device", default="", help="Device serial number (empty = auto)"
    )
    args = parser.parse_args()

    ws = WaveShaperUSB(
        dll_dir=args.dll_dir,
        wsconfig=args.wsconfig,
        device_name=args.device,
    )

    try:
        ws.open()
    except Exception as exc:
        # Signal startup failure to parent process and exit
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        sys.stdout.flush()
        sys.exit(1)

    # Signal successful startup
    sys.stdout.write(json.dumps({"ok": True, "value": "ready"}) + "\n")
    sys.stdout.flush()

    for raw_line in sys.stdin:
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            req = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            resp = {"ok": False, "error": f"JSON parse error: {exc}"}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        try:
            resp = _dispatch(ws, req)
        except Exception as exc:
            resp = {"ok": False, "error": str(exc)}

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()

        if resp.get("value") == "__close__":
            break

    ws.close()


if __name__ == "__main__":
    main()
