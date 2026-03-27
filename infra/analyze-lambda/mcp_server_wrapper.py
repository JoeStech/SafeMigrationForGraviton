"""Wrapper that sets LD_PRELOAD before importing sentence-transformers/torch.

The shim intercepts openat() (which glibc's fopen calls internally) for
/sys/devices/system/cpu/possible and /sys/devices/system/cpu/present,
returning "0\n" when those files are absent in Lambda's sandbox.

LD_PRELOAD must be set before any C library calls happen, so we re-exec
this process with LD_PRELOAD in the environment if it isn't already set.
"""
import os
import sys

SHIM = "/app/cpu_shim.so"

# Re-exec with LD_PRELOAD if the shim exists and isn't loaded yet
if os.path.exists(SHIM) and SHIM not in os.environ.get("LD_PRELOAD", ""):
    env = os.environ.copy()
    existing = env.get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = f"{SHIM}:{existing}" if existing else SHIM
    env["_CPU_SHIM_LOADED"] = "1"
    os.execve(sys.executable, [sys.executable] + sys.argv, env)
    # execve replaces this process — nothing below runs unless execve fails

import runpy
runpy.run_path("/app/server.py", run_name="__main__")
