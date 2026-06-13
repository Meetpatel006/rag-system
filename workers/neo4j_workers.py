import os
import time
import uuid
import tempfile
import traceback
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from parta.logger import logger

import requests

from parta.processing.ingest_neo4j import run_neo4j_batch

SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:8004")
WORKER_ID  = f"neo4j-{uuid.uuid4().hex[:6]}"
BASE_DIR   = Path(__file__).resolve().parent.parent / "parta"

# ── persistent session — reuses TCP connection across all poll cycles ─────────
_session = requests.Session()

logger.info("=" * 80)
logger.info(f"[{WORKER_ID}] NEO4J WORKER STARTED (batch mode)")
logger.info(f"[{WORKER_ID}] SERVER   : {SERVER_URL}")
logger.info(f"[{WORKER_ID}] BASE_DIR : {BASE_DIR}")
logger.info("=" * 80)
is_connected = False

wait_count = 0
MAX_WAITS = 15

while True:
    job_id  = None
    book_id = None
    local_ready_path = None

    try:
        r = _session.get(
            f"{SERVER_URL}/get_neo4j_job",
            params={"worker_id": WORKER_ID},
            timeout=1800,
        )

        if not is_connected:
            logger.info(f"[{WORKER_ID}] Connected to server")
            is_connected = True

        if r.status_code != 200:
            logger.error(f"[{WORKER_ID}] get_neo4j_job failed: HTTP {r.status_code}")
            time.sleep(5)
            continue

        job = r.json()
        if job.get("action") != "PROCESS":
            wait_count += 1
            if wait_count >= MAX_WAITS:
                logger.info(f"[{WORKER_ID}] No jobs received for {MAX_WAITS * 2}s. Exiting gracefully.")
                break
            time.sleep(2)
            continue
        
        wait_count = 0

        job_id       = job.get("job_id")
        book_id      = job.get("book_id")
        batch_start  = job.get("start_offset", 0)
        batch_count  = job.get("page_count", 0)
        batch_idx    = job.get("chunk_idx", 0)

        logger.info("\n" + "=" * 80)
        logger.info(f"[{WORKER_ID}] NEW NEO4J BATCH JOB")
        logger.info(f"[{WORKER_ID}] JOB ID     : {job_id}")
        logger.info(f"[{WORKER_ID}] BOOK ID    : {book_id}")
        logger.info(f"[{WORKER_ID}] BATCH      : #{batch_idx} (chunks {batch_start}–{batch_start + batch_count - 1})")
        logger.info("=" * 80)

        # ── download ready.json from server ───────────────────────────────────
        logger.info(f"[{WORKER_ID}] Downloading ready file for '{book_id}'...")
        r2 = _session.get(
            f"{SERVER_URL}/download_ready/{book_id}",
            timeout=1800,
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

        logger.info(f"[{WORKER_ID}] Ready file saved to {local_ready_path}")

        # ── run batch ingestion ────────────────────────────────────────────────
        logger.info(f"[{WORKER_ID}] Starting Neo4j batch ingestion (chunks {batch_start}–{batch_start + batch_count - 1})...")
        start_time = time.time()

        result = run_neo4j_batch(
            book_id=book_id,
            ready_path=local_ready_path,
            base_dir=str(BASE_DIR),
            batch_start=batch_start,
            batch_count=batch_count,
        )

        elapsed = round(time.time() - start_time, 2)
        logger.info(f"[{WORKER_ID}] Neo4j batch completed in {elapsed}s — entities={result.get('entities_written', 0)}, specs={result.get('specs_written', 0)}")

        # ── submit success ─────────────────────────────────────────────────────
        response = _session.post(
            f"{SERVER_URL}/submit_neo4j_result",
            json={
                "job_id":    job_id,
                "worker_id": WORKER_ID,
                "success":   True,
                "content":   result,
            },
            timeout=1800,
        )

        if response.status_code == 200:
            logger.info(f"[{WORKER_ID}] Completion acknowledged for batch #{batch_idx} of '{book_id}'")
        else:
            logger.error(f"[{WORKER_ID}] submit_neo4j_result failed: HTTP {response.status_code}")

        logger.info("=" * 80)

    except requests.exceptions.ConnectionError:
        if is_connected:
            logger.error(f"[{WORKER_ID}] Disconnected from server. Waiting to reconnect...")
            is_connected = False
        time.sleep(5)

    except Exception as e:
        logger.error(f"[{WORKER_ID}] Neo4j worker error: {e}")
        logger.error(traceback.format_exc())

        if job_id:
            try:
                _session.post(
                    f"{SERVER_URL}/submit_neo4j_result",
                    json={
                        "job_id":    job_id,
                        "worker_id": WORKER_ID,
                        "success":   False,
                        "content":   "",
                    },
                    timeout=1800,
                )
            except Exception as e:
                logger.error(f"[{WORKER_ID}] Error submitting failure: {e}")
                logger.error(traceback.format_exc())

        time.sleep(5)

    finally:
        # ── always clean up the temp file ──────────────────────────────────────
        if local_ready_path:
            try:
                Path(local_ready_path).unlink(missing_ok=True)
            except Exception:
                pass
