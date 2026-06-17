import logging
import os
import sys
import functools
import time
import socket
from pathlib import Path

# Provide a structured logger for the workers
logger = logging.getLogger("worker_logger")
logger.setLevel(logging.INFO)
logger.propagate = False

_worker_configured = False

def setup_worker_logger(worker_type: str, worker_id: str):
    """
    Sets up the logger for a specific worker type and ID.
    This creates logs in the `logs/{worker_type}` directory.
    The log file name corresponds to the worker's 6-digit UUID.
    """
    global _worker_configured
    if _worker_configured:
        return

    try:
        # Get the IP address to easily track logs
        ip_addr = socket.gethostbyname(socket.gethostname())
    except Exception:
        ip_addr = "127.0.0.1"

    # Define the logs directory structure: RAG/workers/logs/<worker_type>
    base_dir = Path(__file__).resolve().parent
    logs_dir = base_dir / "logs" / worker_type
    logs_dir.mkdir(parents=True, exist_ok=True)

    # We expect worker_id to contain the uuid 6 digits, e.g. text-a1b2c3
    log_file = logs_dir / f"{worker_id}.log"

    fmt = logging.Formatter(
        f"%(asctime)s  %(levelname)-5s  [IP: {ip_addr}]  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8", mode="a")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    _worker_configured = True

def _label(func) -> str:
    """Return function's qualname for logging."""
    return func.__qualname__

def worker_log_process(worker_id: str):
    """
    Decorator for worker processes, tracking START and DONE times.
    """
    def decorator(func):
        _name = _label(func)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger.info(f"┌─ START  [{worker_id}] {_name}")
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - t0
                logger.info(f"└─ DONE   [{worker_id}] {_name:<38}  [total: {elapsed:.3f}s]")
                return result
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                logger.error(f"└─ FAILED [{worker_id}] {_name:<38}  [{elapsed:.3f}s]  {exc}")
                raise
        return wrapper
    return decorator
