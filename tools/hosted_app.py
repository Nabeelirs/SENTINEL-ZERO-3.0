"""Prepared hosting entry point; publication/licensing approval is still required."""
from pathlib import Path

from run_demo import configure
from sentinel_zero.presentation.explorer import main

configure(Path(__file__).resolve().parent)
main()
