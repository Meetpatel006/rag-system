import os
import sys
from pathlib import Path
from sentence_transformers import SentenceTransformer
from transformers import AutoModel

# Read HF_TOKEN from environment; fall back to CLI arg if provided
hf_token = os.environ.get("HF_TOKEN") or (sys.argv[1] if len(sys.argv) > 1 else "")
if hf_token:
    os.environ["HF_TOKEN"] = hf_token
else:
    print("Warning: HF_TOKEN not set. Set the HF_TOKEN environment variable or pass it as a CLI argument.")

repo_root = Path(os.path.abspath('..'))
portable_dir = repo_root / "parta" / "portable"
nomic_dir = portable_dir / "nomic"
reranker_dir = portable_dir / "jina-reranker-v3"
colbert_dir = portable_dir / "colbert"

nomic_dir.mkdir(parents=True, exist_ok=True)
reranker_dir.mkdir(parents=True, exist_ok=True)
colbert_dir.mkdir(parents=True, exist_ok=True)

print(f"Downloading Reranker to {reranker_dir}...")
# Jina Reranker v3 — 131K context, listwise state-of-the-art reranker
reranker = AutoModel.from_pretrained("jinaai/jina-reranker-v3", trust_remote_code=True)
reranker.save_pretrained(str(reranker_dir))

print(f"Downloading Nomic to {nomic_dir}...")
# Best open-source embeddings model
nomic = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
nomic.save(str(nomic_dir))

print(f"Downloading ColBERT to {colbert_dir}...")
# ColBERT v2 — multi-vector late interaction model for hybrid search
from fastembed import LateInteractionTextEmbedding
colbert = LateInteractionTextEmbedding("colbert-ir/colbertv2.0", cache_dir=str(colbert_dir))
# fastembed handles caching internally — model is now downloaded to portable dir

print("✅ All missing offline models downloaded successfully!")
