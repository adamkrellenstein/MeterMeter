"""Shared test configuration: add the nvim Python path and benchmarks path to sys.path."""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_NVIM_PY = os.path.abspath(os.path.join(_HERE, "..", "nvim", "metermeter.nvim", "python"))
_BENCH_PY = os.path.abspath(os.path.join(_HERE, "..", "benchmarks"))

if _NVIM_PY not in sys.path:
    sys.path.insert(0, _NVIM_PY)
if _BENCH_PY not in sys.path:
    sys.path.insert(0, _BENCH_PY)
