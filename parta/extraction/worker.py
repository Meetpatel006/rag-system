"""
extraction/worker.py
--------------------
Single worker for PDF extraction.
Designed to run alongside extraction_server.py.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import time
import requests
import uuid
import tempfile
import os
from pypdf import PdfReader
from parta.logger import time_it

SERVER_URL = "http://localhost:8004"
WORKER_ID = f"worker-{uuid.uuid4().hex[:6]}"

@time_it
def process_chunk(pdf_bytes: bytes, start_offset: int) -> str:
    # Save bytes to a temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        reader = PdfReader(tmp_path)
        content = ""
        for i, page in enumerate(reader.pages):
            text = page.extract_text()
            if text:
                content += f"\n\n## Page {start_offset + i + 1}\n\n{text}"
        return content
    finally:
        os.remove(tmp_path)

def start_worker():
    print(f"[{WORKER_ID}] Starting single extraction worker...")
    
    while True:
        try:
            resp = requests.get(f"{SERVER_URL}/get_job", params={"worker_id": WORKER_ID})
            
            if resp.status_code != 200:
                time.sleep(5)
                continue
                
            data = resp.json()
            action = data.get("action")
            
            if action == "WAIT":
                time.sleep(2)
                continue
                
            if action == "PROCESS":
                job_id = data["job_id"]
                book_id = data["book_id"]
                chunk_idx = data["chunk_idx"]
                start_offset = data.get("start_offset", 0)
                
                print(f"[{WORKER_ID}] Got chunk {chunk_idx} for book {book_id}. Downloading...")
                
                chunk_resp = requests.get(f"{SERVER_URL}/chunk/{job_id}", stream=True)
                if chunk_resp.status_code == 200:
                    try:
                        content = process_chunk(chunk_resp.content, start_offset)
                        print(f"[{WORKER_ID}] Extraction success for chunk {chunk_idx}. Submitting...")
                        
                        requests.post(f"{SERVER_URL}/submit_result", json={
                            "job_id": job_id,
                            "worker_id": WORKER_ID,
                            "success": True,
                            "content": content
                        })
                    except Exception as e:
                        print(f"[{WORKER_ID}] Extraction failed for chunk {chunk_idx}: {e}")
                        requests.post(f"{SERVER_URL}/submit_result", json={
                            "job_id": job_id,
                            "worker_id": WORKER_ID,
                            "success": False,
                            "content": ""
                        })
                else:
                    print(f"[{WORKER_ID}] Failed to download chunk {job_id}")
                    
        except requests.exceptions.ConnectionError:
            print(f"[{WORKER_ID}] Cannot reach server at {SERVER_URL}. Waiting...")
            time.sleep(5)
        except Exception as e:
            print(f"[{WORKER_ID}] Error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    start_worker()
