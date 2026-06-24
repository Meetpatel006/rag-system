"""
extract_pdf.py
Standalone script: take a PDF, run it through the extraction worker
pipeline (server + text worker), save result as .md.

Usage:
    python extract_pdf.py path/to/document.pdf
    python extract_pdf.py path/to/document.pdf --book-id my_custom_id --ocr
"""

import argparse
import os
import sys
import time
import subprocess
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SERVER_PORT = "8004"
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

# Use conda rag_env if available, otherwise fall back to current interpreter
_CONDA_PYTHON = None
for candidate in [
    Path(os.environ.get("CONDA_PREFIX", "")) / "python.exe",
    Path("C:\\Users\\hites\\miniconda3\\envs\\rag_env\\python.exe"),
]:
    if candidate.exists():
        _CONDA_PYTHON = str(candidate)
        break
_PYTHON = _CONDA_PYTHON or sys.executable


def _wait_for_server(timeout: int = 30) -> bool:
    import requests
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{SERVER_URL}/health", timeout=3)
            if r.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    return False


def _wait_for_worker(timeout: int = 15) -> bool:
    import requests
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{SERVER_URL}/get_job?worker_id=text-probe", timeout=3)
            if r.status_code == 200:
                return True
        except requests.exceptions.ConnectionError:
            pass
        time.sleep(1)
    return False


def main():
    parser = argparse.ArgumentParser(description="Extract PDF text via worker pipeline")
    parser.add_argument("pdf", type=str, help="Path to the PDF file")
    parser.add_argument("--book-id", type=str, default=None,
                        help="Book ID (defaults to PDF filename stem)")
    parser.add_argument("--ocr", action="store_true",
                        help="Enable OCR (Docling OCR pipeline)")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for .md (defaults to data/processed/)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel text workers (default: 1)")
    args = parser.parse_args()

    pdf_path = Path(args.pdf).resolve()
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    book_id = args.book_id or pdf_path.stem
    base_dir = Path(__file__).resolve().parent

    output_dir = Path(args.output_dir) if args.output_dir else base_dir / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{book_id}.md"

    (base_dir / "data").mkdir(parents=True, exist_ok=True)

    # ── Start extraction server ──────────────────────────────────────────────
    print(f"[extract] Starting extraction server on port {SERVER_PORT}...")
    server_log = open(base_dir / "data" / "_server.log", "w", encoding="utf-8")
    server_proc = subprocess.Popen(
        [_PYTHON, "-m", "uvicorn", "parta.extraction.extraction_server:app",
         "--host", "0.0.0.0", "--port", SERVER_PORT, "--log-level", "warning"],
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    if not _wait_for_server():
        server_proc.kill()
        print("ERROR: Extraction server did not start in time.", file=sys.stderr)
        sys.exit(1)
    print("[extract] Server ready.")

    # ── Start text workers ───────────────────────────────────────────────────
    n_workers = args.workers
    print(f"[extract] Starting {n_workers} text worker(s)...")
    env = os.environ.copy()
    env["SERVER_URL"] = SERVER_URL
    worker_procs = []
    worker_logs = []
    for i in range(n_workers):
        log = open(base_dir / "data" / f"_worker_{i}.log", "w", encoding="utf-8")
        worker_logs.append(log)
        proc = subprocess.Popen(
            [_PYTHON, str(base_dir / "workers" / "text_workers.py")],
            env=env, stdout=log, stderr=subprocess.STDOUT,
        )
        worker_procs.append(proc)

    if not _wait_for_worker():
        for p in worker_procs:
            p.kill()
        server_proc.kill()
        print("ERROR: No worker connected in time.", file=sys.stderr)
        sys.exit(1)
    print(f"[extract] {n_workers} worker(s) ready.")

    # ── Run extraction ───────────────────────────────────────────────────────
    try:
        from parta.extraction.master import run_extraction
        print(f"[extract] Processing {pdf_path.name} (ocr={'yes' if args.ocr else 'no'})...")
        result_path = run_extraction(
            book_id=book_id,
            pdf_path=str(pdf_path),
            base_dir=str(base_dir),
            ocr_enabled=args.ocr,
        )
        print(f"[extract] Done → {result_path}")
    except Exception as e:
        print(f"ERROR: Extraction failed: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[extract] Interrupted.")
    finally:
        print("[extract] Stopping workers and server...")
        for p in worker_procs:
            p.terminate()
        server_proc.terminate()
        time.sleep(2)
        for p in worker_procs:
            if p.poll() is None:
                p.kill()
        if server_proc.poll() is None:
            server_proc.kill()
        for lf in worker_logs:
            lf.close()
        server_log.close()
        print("[extract] Logs → data/_worker_*.log, data/_server.log")


if __name__ == "__main__":
    main()
