import modal
import subprocess
import time

app = modal.App("ollama-medium-model")
MODEL = "mistral:7b"

def pull_model():
    subprocess.Popen(["ollama", "serve"])
    time.sleep(5)
    subprocess.run(["ollama", "pull", MODEL])

ollama_image = (
    modal.Image.debian_slim(python_version="3.10")
    .apt_install("curl", "systemctl", "zstd")
    .run_commands("curl -fsSL https://ollama.com/install.sh | sh")
    .pip_install("ollama")
    .env({"OLLAMA_HOST": "0.0.0.0"})
    .run_function(pull_model)
)

@app.cls(gpu="A10G", image=ollama_image, scaledown_window=300)
class OllamaMedium:
    @modal.web_server(port=11434, startup_timeout=300)
    def serve(self):
        subprocess.Popen(["ollama", "serve"])
