import os
import sys
import re
import asyncio
import time
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Monitor is now ready to receive logs from manually started workers
    yield

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for worker outputs, keyed by unique worker_id
workers_state = {}

MAX_LOG_LINES = 5000

from pydantic import BaseModel

class LogEntry(BaseModel):
    log: str

@app.post("/api/log")
async def receive_log(entry: LogEntry, request: Request):
    client_ip = request.client.host
    text = entry.log.strip()
    if not text:
        return {"status": "ignored"}
        
    # Extract worker ID
    id_match = re.search(r"\[(worker-[a-f0-9]+|qdrant-[a-f0-9]+|neo4j-[a-f0-9]+)\]", text)
    if not id_match:
        return {"status": "ignored"}
        
    worker_id = id_match.group(1)
    
    if worker_id.startswith("worker-"): w_type = "Text"
    elif worker_id.startswith("qdrant-"): w_type = "Qdrant"
    elif worker_id.startswith("neo4j-"): w_type = "Neo4j"
    else: w_type = "Unknown"
    
    if worker_id not in workers_state:
        workers_state[worker_id] = {
            "type": w_type,
            "id": worker_id,
            "client_ip": client_ip,
            "server_url": "Unknown",
            "status": "Running (Online)",
            "logs": [],
            "last_seen": time.time()
        }
    
    # Update last seen and IP
    workers_state[worker_id]["client_ip"] = client_ip
    workers_state[worker_id]["last_seen"] = time.time()
    
    if "Disconnected" in text or "Exiting gracefully" in text or "FAILED" in text:
        workers_state[worker_id]["status"] = "Offline / Disconnected"
    else:
        workers_state[worker_id]["status"] = "Running (Online)"
    
    workers_state[worker_id]["logs"].append(text)
    if len(workers_state[worker_id]["logs"]) > MAX_LOG_LINES:
        workers_state[worker_id]["logs"].pop(0)
        
    if "SERVER   :" in text:
        parts = text.split("SERVER   :")
        if len(parts) > 1:
            workers_state[worker_id]["server_url"] = parts[1].strip()
            
    return {"status": "ok"}


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Worker Monitor Dashboard (Multi-IP View)</title>
    <style>
        :root {
            --bg-color: #0f172a;
            --surface-color: #1e293b;
            --border-color: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --accent: #3b82f6;
            --success: #10b981;
            --error: #ef4444;
            --warning: #f59e0b;
        }

        body {
            margin: 0;
            padding: 0;
            font-family: 'Inter', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
        }

        .container {
            max-width: 1600px;
            margin: 0 auto;
            padding: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--border-color);
        }

        h1 {
            font-size: 1.5rem;
            font-weight: 600;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        h1 span {
            color: var(--accent);
        }

        .subtitle {
            font-size: 0.85rem;
            color: var(--text-secondary);
            display: block;
            margin-top: 0.25rem;
        }

        .workers-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
            gap: 1.5rem;
        }

        .worker-card {
            background-color: var(--surface-color);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .worker-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }

        .card-header {
            padding: 1.25rem;
            border-bottom: 1px solid var(--border-color);
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            background: rgba(0,0,0,0.1);
        }

        .worker-info h2 {
            margin: 0 0 0.25rem 0;
            font-size: 1.25rem;
            font-weight: 600;
        }

        .worker-id {
            font-family: monospace;
            color: var(--text-secondary);
            font-size: 0.875rem;
            background: rgba(255,255,255,0.05);
            padding: 2px 6px;
            border-radius: 4px;
        }

        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.375rem;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .status-badge.online {
            background-color: rgba(16, 185, 129, 0.1);
            color: var(--success);
            border: 1px solid rgba(16, 185, 129, 0.2);
        }

        .status-badge.offline {
            background-color: rgba(239, 68, 68, 0.1);
            color: var(--error);
            border: 1px solid rgba(239, 68, 68, 0.2);
        }

        .status-badge::before {
            content: '';
            width: 6px;
            height: 6px;
            border-radius: 50%;
        }

        .status-badge.online::before {
            background-color: var(--success);
            box-shadow: 0 0 8px var(--success);
        }

        .status-badge.offline::before {
            background-color: var(--error);
        }

        .card-body {
            padding: 1.25rem;
            flex-grow: 1;
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .meta-row {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            font-size: 0.875rem;
            color: var(--text-secondary);
        }

        .meta-label {
            font-weight: 600;
            color: var(--text-primary);
        }
        
        .ip-badge {
            background: rgba(59, 130, 246, 0.1);
            color: var(--accent);
            padding: 2px 8px;
            border-radius: 4px;
            font-family: monospace;
            font-weight: bold;
            border: 1px solid rgba(59, 130, 246, 0.2);
        }

        .terminal {
            background-color: #000;
            border-radius: 8px;
            padding: 1rem;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 0.8125rem;
            color: #a3be8c;
            height: 350px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 0.35rem;
            border: 1px solid #111;
        }

        .terminal::-webkit-scrollbar {
            width: 8px;
        }

        .terminal::-webkit-scrollbar-track {
            background: #111;
            border-radius: 4px;
        }

        .terminal::-webkit-scrollbar-thumb {
            background: #333;
            border-radius: 4px;
        }

        .log-line {
            line-height: 1.4;
            word-break: break-all;
        }
        
        .log-meta {
            color: #5c6370;
        }
        
        .log-info { color: #61afef; }
        .log-error { color: #e06c75; }
        .log-warn { color: #d19a66; }

        .refresh-indicator {
            font-size: 0.875rem;
            color: var(--text-secondary);
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        .spinner {
            width: 12px;
            height: 12px;
            border: 2px solid var(--border-color);
            border-top-color: var(--accent);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @media (max-width: 768px) {
            .workers-grid {
                grid-template-columns: 1fr;
            }
        }
        
        .empty-state {
            grid-column: 1 / -1;
            text-align: center;
            padding: 4rem;
            color: var(--text-secondary);
            background: rgba(255,255,255,0.02);
            border: 1px dashed var(--border-color);
            border-radius: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>⚡ Multi-IP <span>Worker Monitor</span></h1>
                <span class="subtitle">Dynamically tracking unlimited distributed workers over HTTP</span>
            </div>
            <div class="refresh-indicator">
                <div class="spinner"></div>
                Live Stream
            </div>
        </header>

        <div class="workers-grid" id="workers-container">
            <!-- Rendered by JS -->
            <div class="empty-state">
                <h2>No workers connected yet</h2>
                <p>Start a worker process and its logs will appear here automatically.</p>
            </div>
        </div>
    </div>

    <script>
        const API_URL = '/api/status';

        function highlightLog(logText) {
            let escaped = logText.replace(/</g, '&lt;').replace(/>/g, '&gt;');
            
            if (escaped.includes('INFO')) escaped = escaped.replace('INFO', '<span class="log-info">INFO</span>');
            if (escaped.includes('ERROR')) escaped = escaped.replace('ERROR', '<span class="log-error">ERROR</span>');
            if (escaped.includes('WARN')) escaped = escaped.replace('WARN', '<span class="log-warn">WARN</span>');
            
            escaped = escaped.replace(/^(\\d{4}-\\d{2}-\\d{2}\\s\\d{2}:\\d{2}:\\d{2})/, '<span class="log-meta">$1</span>');

            return `<div class="log-line">${escaped}</div>`;
        }

        function createWorkerCard(worker) {
            const isOnline = worker.status.includes('Running');
            const badgeClass = isOnline ? 'online' : 'offline';

            const logsHtml = worker.logs.map(log => highlightLog(log)).join('');

            return `
                <div class="worker-card">
                    <div class="card-header">
                        <div class="worker-info">
                            <h2>${worker.type} Worker</h2>
                            <span class="worker-id">${worker.id}</span>
                        </div>
                        <div class="status-badge ${badgeClass}">${worker.status}</div>
                    </div>
                    <div class="card-body">
                        <div class="meta-row" style="justify-content: space-between;">
                            <div style="display: flex; gap: 0.5rem; align-items: center;">
                                <span class="meta-label">Worker IP:</span>
                                <span class="ip-badge">${worker.client_ip}</span>
                            </div>
                            <div style="display: flex; gap: 0.5rem; align-items: center; font-size: 0.75rem;">
                                <span class="meta-label">Server:</span>
                                <span>${worker.server_url}</span>
                            </div>
                        </div>
                        <div class="terminal" id="term-${worker.id}">
                            ${logsHtml || '<div style="color: #5c6370; text-align: center; margin-top: 2rem;">Waiting for logs...</div>'}
                        </div>
                    </div>
                </div>
            `;
        }

        let isUserScrolling = {};

        async function fetchStatus() {
            try {
                const response = await fetch(API_URL);
                const data = await response.json();
                
                const container = document.getElementById('workers-container');
                const workers = Object.values(data);
                
                if (workers.length === 0) return;
                
                // Track scroll state before update
                workers.forEach(worker => {
                    const term = document.getElementById(`term-${worker.id}`);
                    if (term) {
                        isUserScrolling[worker.id] = term.scrollHeight - term.scrollTop > term.clientHeight + 10;
                    }
                });

                // Sort by IP, then by Type, then by ID
                workers.sort((a, b) => {
                    if (a.client_ip !== b.client_ip) return a.client_ip.localeCompare(b.client_ip);
                    if (a.type !== b.type) return a.type.localeCompare(b.type);
                    return a.id.localeCompare(b.id);
                });

                container.innerHTML = workers.map(w => createWorkerCard(w)).join('');

                // Auto scroll to bottom of terminals if user wasn't scrolling up
                workers.forEach(worker => {
                    const term = document.getElementById(`term-${worker.id}`);
                    if (term && !isUserScrolling[worker.id]) {
                        term.scrollTop = term.scrollHeight;
                    }
                });

            } catch (err) {
                console.error('Failed to fetch status', err);
            }
        }

        fetchStatus();
        setInterval(fetchStatus, 1000);
    </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return HTMLResponse(content=HTML_TEMPLATE, status_code=200)

@app.get("/api/status")
async def get_status():
    # Mark workers as offline if no logs for 60 seconds
    now = time.time()
    for w in workers_state.values():
        if now - w["last_seen"] > 60:
            w["status"] = "Offline (Timed out)"
            
    return workers_state

if __name__ == "__main__":
    print("Starting Multi-IP Worker Monitor...")
    print("View the live dashboard at: http://127.0.0.1:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
