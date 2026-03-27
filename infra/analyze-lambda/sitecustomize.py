"""sitecustomize.py — runs before any other import in the Python process.

Patches builtins.open so that reads of the cpuinfo /sys paths that don't
exist in Lambda's sandbox return a safe single-CPU default instead of
raising FileNotFoundError, which crashes sentence-transformers at import time.
"""
import builtins
import io

_CPU_SYS_PATHS = {
    "/sys/devices/system/cpu/possible": "0\n",
    "/sys/devices/system/cpu/present": "0\n",
}

_real_open = builtins.open


def _patched_open(file, mode="r", *args, **kwargs):
    if isinstance(file, str) and file in _CPU_SYS_PATHS and ("r" in mode or mode == ""):
        import os
        if not os.path.exists(file):
            return io.StringIO(_CPU_SYS_PATHS[file])
    return _real_open(file, mode, *args, **kwargs)


builtins.open = _patched_open
