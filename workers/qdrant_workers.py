import os
import tempfile
import time
import traceback
import uuid
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from parta.logger import logger

import requests
from parta.processing.ingest_qdrant import run_qdrant_batch

SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:8004")
WORKER_ID = f"qdrant-{uuid.uuid4().hex[:6]}"
BASE_DIR = Path(__file__).resolve().parent.parent

# ── persistent session — reuses TCP connection across all poll cycles ─────────
_session = requests.Session()

logger.info("=" * 80)
logger.info(f"[{WORKER_ID}] QDRANT WORKER STARTED (batch mode)")
logger.info(f"[{WORKER_ID}] SERVER   : {SERVER_URL}")
logger.info(f"[{WORKER_ID}] BASE_DIR : {BASE_DIR}")
logger.info("=" * 80)
is_connected = False

while True:
    job_id = None
    local_ready_path = None
    local_prop_path = None

    try:
        r = _session.get(
            f"{SERVER_URL}/get_qdrant_job",
            params={"worker_id": WORKER_ID},
            timeout=30,
        )

        if not is_connected:
            logger.info(f"[{WORKER_ID}] Connected to server")
            is_connected = True

        if r.status_code != 200:
            logger.error(f"[{WORKER_ID}] Failed to get job ({r.status_code})")
            time.sleep(5)
            continue

        job = r.json()

        if job.get("action") != "PROCESS":
            time.sleep(2)
            continue

        job_id       = job["job_id"]
        book_id      = job["book_id"]
        batch_start  = job.get("start_offset", 0)
        batch_count  = job.get("page_count", 0)
        batch_idx    = job.get("chunk_idx", 0)
        # batch_kind is encoded in chunk_path field by the server
        batch_kind   = job.get("chunk_path", "propositions")
        # ready_path and prop_path from job response are server-side paths — do not use directly

        logger.info("\n" + "=" * 80)
        logger.info(f"[{WORKER_ID}] NEW QDRANT BATCH JOB")
        logger.info(f"[{WORKER_ID}] JOB ID     : {job_id}")
        logger.info(f"[{WORKER_ID}] BOOK ID    : {book_id}")
        logger.info(f"[{WORKER_ID}] KIND       : {batch_kind}")
        logger.info(f"[{WORKER_ID}] BATCH      : #{batch_idx} ({batch_kind} {batch_start}–{batch_start + batch_count - 1})")
        logger.info("=" * 80)

        # ── download ready.json from server ───────────────────────────────────
        logger.info(f"[{WORKER_ID}] Downloading ready file for '{book_id}'...")
        r2 = _session.get(
            f"{SERVER_URL}/download_ready/{book_id}",
            timeout=60,
        )
        if r2.status_code != 200:
            raise RuntimeError(
                f"download_ready failed: HTTP {r2.status_code} — {r2.text[:200]}"
            )

        with tempfile.NamedTemporaryFile(
            mode="wb", suffix="_ready.json", delete=False
        ) as f:
            f.write(r2.content)
            local_ready_path = f.name

        # ── download prop.json from server ────────────────────────────────────
        logger.info(f"[{WORKER_ID}] Downloading prop file for '{book_id}'...")
        r3 = _session.get(
            f"{SERVER_URL}/download_prop/{book_id}",
            timeout=60,
        )
        if r3.status_code != 200:
            raise RuntimeError(
                f"download_prop failed: HTTP {r3.status_code} — {r3.text[:200]}"
            )

        with tempfile.NamedTemporaryFile(
            mode="wb", suffix="_prop.json", delete=False
        ) as f:
            f.write(r3.content)
            local_prop_path = f.name

        logger.info(f"[{WORKER_ID}] Files ready — running batch ingestion ({batch_kind} {batch_start}–{batch_start + batch_count - 1})...")

        # ── run batch ingestion ────────────────────────────────────────────────
        start_time = time.time()

        chunks_stored = run_qdrant_batch(
            book_id=book_id,
            ready_path=local_ready_path,
            prop_path=local_prop_path,
            base_dir=str(BASE_DIR),
            batch_start=batch_start,
            batch_count=batch_count,
            batch_kind=batch_kind,
        )

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"[{WORKER_ID}] Qdrant batch completed in {elapsed}s — stored={chunks_stored}")

        # ── submit success ─────────────────────────────────────────────────────
        response = _session.post(
            f"{SERVER_URL}/submit_qdrant_result",
            json={
                "job_id": job_id,
                "worker_id": WORKER_ID,
                "success": True,
                "content": {
                    "chunks_stored": chunks_stored,
                    "batch_kind": batch_kind,
                    "batch_start": batch_start,
                    "batch_count": batch_count,
                }
            },
            timeout=30,
        )

        logger.info(f"[{WORKER_ID}] Completed batch #{batch_idx} ({batch_kind}) status={response.status_code}")

    except requests.exceptions.ConnectionError:
        if is_connected:
            logger.error(f"[{WORKER_ID}] Disconnected from server. Waiting to reconnect...")
            is_connected = False
        time.sleep(5)

    except Exception as e:
        logger.error(f"[{WORKER_ID}] Error: {e}")
        logger.error(traceback.format_exc())

        if job_id:
            try:
                _session.post(
                    f"{SERVER_URL}/submit_qdrant_result",
                    json={
                        "job_id": job_id,
                        "worker_id": WORKER_ID,
                        "success": False,
                        "error": str(e),
                    },
                    timeout=10,
                )
            except Exception:
                pass

        time.sleep(5)

    finally:
        # ── always clean up temp files ─────────────────────────────────────────
        for p in (local_ready_path, local_prop_path):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
