# 🚀 Scaling RAG Workers on Modal

Your RAG workers (`Text`, `Neo4j`, and `Qdrant`) are now permanently deployed on Modal's cloud infrastructure as the `rag-workers` app. They have the offline GLiNER and Nomic models baked in, meaning they start instantly without any large downloads.

Here is how you can use, scale, and manage them.

---

## 1. Starting Workers via CLI

You can easily spin up a fleet of workers using the Modal CLI. By default, it summons 5 workers per task type (15 total workers).

Run the following command from your terminal:
```powershell
modal run modal_workers.py::summon --workers-count 5 --server-url "https://your-ngrok-or-production-server.com"
```
*Note: Because the workers are running in the cloud, they cannot connect to `http://127.0.0.1`. You must expose your local server using Ngrok, Cloudflare Tunnels, or provide your production server URL.*

---

## 2. Triggering Workers Dynamically via Python (Backend Integration)

Since the app is deployed, you don't even need the CLI! You can dynamically spin up workers on-demand directly from your Python backend (e.g., inside FastAPI when a user uploads a new textbook).

```python
import modal

def start_cloud_workers(server_url: str, count: int = 5):
    print(f"Summoning {count} workers in the cloud...")
    
    # Connect to the deployed Modal functions
    text_worker = modal.Function.lookup("rag-workers", "run_text_worker")
    neo4j_worker = modal.Function.lookup("rag-workers", "run_neo4j_worker")
    qdrant_worker = modal.Function.lookup("rag-workers", "run_qdrant_worker")
    
    for _ in range(count):
        # .spawn() triggers the function in the cloud in the background and returns immediately
        text_worker.spawn(server_url)
        neo4j_worker.spawn(server_url)
        qdrant_worker.spawn(server_url)
        
    print("Workers are now processing jobs in the background!")

# Example Usage:
# start_cloud_workers("https://api.my-rag-app.com")
```

---

## 3. How It Works

1. **Independent & Portable:** Each worker is completely isolated in its own container with a maximum concurrency defined in `modal_workers.py` (currently `max_containers=5`).
2. **Long-Running:** Each container has a timeout of `86400` seconds (24 hours). They run a continuous `while True` loop pulling jobs from your server URL.
3. **Auto-Scaling:** Modal will automatically spin up multiple containers in parallel to handle the workload concurrently.

---

## 4. Monitoring and Stopping Workers

You can monitor the live logs, see how many workers are active, and manually stop them at any time from the Modal Dashboard:

👉 **[View your RAG Workers Dashboard](https://modal.com/apps/gcet/main/deployed/rag-workers)**

If you want to stop all active workers running in the background, you can click "Stop" directly on the Modal Dashboard, or simply let them finish their jobs (they will automatically exit or time out eventually).
