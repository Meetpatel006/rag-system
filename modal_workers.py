import modal
import os
import sys

app = modal.App("rag-workers")

# Download function that runs in the cloud during image build
def download_offline_models():
    import os
    from huggingface_hub import snapshot_download
    import nltk

    # Define paths matching the hardcoded paths in ingest scripts
    portable_dir = "/root/RAG/parta/portable"
    gliner_dir = os.path.join(portable_dir, "gliner")
    nomic_dir = os.path.join(portable_dir, "nomic")
    nltk_dir = os.path.join(portable_dir, "nltk_data")

    os.makedirs(gliner_dir, exist_ok=True)
    os.makedirs(nomic_dir, exist_ok=True)
    os.makedirs(nltk_dir, exist_ok=True)

    print("Downloading GLiNER model...")
    snapshot_download(repo_id="urchade/gliner_medium-v2.1", local_dir=gliner_dir)

    print("Downloading Nomic model...")
    snapshot_download(repo_id="nomic-ai/nomic-embed-text-v1.5", local_dir=nomic_dir)

    print("Downloading NLTK data...")
    nltk.download('punkt', download_dir=nltk_dir)
    nltk.download('punkt_tab', download_dir=nltk_dir)
    print("All models downloaded and baked into the image.")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "libgl1-mesa-glx", "libglib2.0-0")
    .pip_install(
        "fastapi", 
        "uvicorn", 
        "pypdf", 
        "PyMuPDF", 
        "requests",
        "neo4j", 
        "qdrant-client", 
        "sentence-transformers", 
        "nltk", 
        "einops", 
        "gliner",
        "huggingface_hub",
        "transformers",
        "torch"
    )
    .run_function(download_offline_models) # Bake 3GB models into image!
    .add_local_dir(
        ".",
        remote_path="/root/RAG",
        ignore=["*.rar", ".git", "__pycache__", "venv", ".venv", "env", "data", "evals", "parta/portable", ".logs", "parta/.logs"]
    )
)

@app.function(
    image=image, 
    timeout=86400, 
    max_containers=5,
    cpu=5.0
)
def run_text_worker(server_url: str):
    print(f"[Modal] Starting text worker connected to {server_url}")
    sys.path.insert(0, "/root/RAG")
    os.environ["SERVER_URL"] = server_url
    
    from workers.text_workers import start_worker
    start_worker()

@app.function(
    image=image, 
    timeout=86400, 
    max_containers=5,
    cpu=5.0,
    gpu="T4"
)
def run_neo4j_worker(server_url: str):
    print(f"[Modal] Starting Neo4j worker connected to {server_url}")
    sys.path.insert(0, "/root/RAG")
    os.environ["SERVER_URL"] = server_url
    
    import workers.neo4j_workers

@app.function(
    image=image, 
    timeout=86400, 
    max_containers=5,
    cpu=5.0
)
def run_qdrant_worker(server_url: str):
    print(f"[Modal] Starting Qdrant worker connected to {server_url}")
    sys.path.insert(0, "/root/RAG")
    os.environ["SERVER_URL"] = server_url
    
    import workers.qdrant_workers

@app.local_entrypoint()
def summon(workers_count: int = 5, server_url: str = "http://YOUR_SERVER_URL:8004"):
    print(f"Summoning {workers_count} workers of each type to Modal infrastructure...")
    
    for i in range(workers_count):
        print(f"  Spawning worker set {i+1}/{workers_count}...")
        run_text_worker.spawn(server_url)
        run_neo4j_worker.spawn(server_url)
        run_qdrant_worker.spawn(server_url)
        
    print(f"\nSummoned {workers_count * 3} total workers successfully!")
    print(f"They are pulling jobs from: {server_url}")
    print("Monitor their progress in the Modal Dashboard.")
