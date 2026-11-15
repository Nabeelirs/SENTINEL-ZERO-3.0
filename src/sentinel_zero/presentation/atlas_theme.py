"""Read the packaged offline stylesheet so each render sees current presentation."""
from importlib.resources import files


def stylesheet() -> str:
    return "<style>" + files("sentinel_zero.presentation").joinpath("atlas.css").read_text(encoding="utf-8") + "</style>"


STYLES = stylesheet()  # Backward-compatible snapshot; live views call stylesheet().
