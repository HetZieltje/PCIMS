"""Small, bounded runtime diagnostics and startup measurements."""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from time import perf_counter

from pcims.contracts import StartupStage

_PROCESS_START = perf_counter()
_STARTUP_STAGES: list[StartupStage] = []
_STAGE_LOCK = Lock()
_LOG_FILE_NAME = "pcims.log"


def mark_startup_stage(name: str) -> None:
    elapsed_ms = round((perf_counter() - _PROCESS_START) * 1000)
    with _STAGE_LOCK:
        if any(stage.name == name for stage in _STARTUP_STAGES):
            return
        _STARTUP_STAGES.append(StartupStage(name, elapsed_ms))


def startup_stages() -> tuple[StartupStage, ...]:
    with _STAGE_LOCK:
        return tuple(_STARTUP_STAGES)


def configure_logging(data_directory: Path) -> Path:
    """Configure one bounded application log beside the database."""

    log_path = data_directory / _LOG_FILE_NAME
    root = logging.getLogger("pcims")
    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == log_path
        for handler in root.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_048_576,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
        if os.name != "nt":
            log_path.chmod(0o600)
    return log_path


def close_logging(path: Path) -> None:
    """Release this application's file handle, including on Windows."""

    root = logging.getLogger("pcims")
    for handler in tuple(root.handlers):
        if (
            isinstance(handler, RotatingFileHandler)
            and Path(handler.baseFilename) == path
        ):
            root.removeHandler(handler)
            handler.close()


def log_path(data_directory: Path) -> Path:
    return data_directory / _LOG_FILE_NAME


def read_log_tail(path: Path, *, maximum_bytes: int = 64 * 1024) -> str:
    """Read a bounded UTF-8 tail without loading an arbitrarily large log."""

    try:
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - maximum_bytes))
            content = stream.read(maximum_bytes)
    except FileNotFoundError:
        return "No application log has been written yet."
    if size > maximum_bytes:
        content = content.partition(b"\n")[2]
    return content.decode("utf-8", errors="replace").strip()
