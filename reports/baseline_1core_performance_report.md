# Performance Baseline Report: 1-Core per Container Architecture

## 1. Execution Summary
- **Document**: `irs-payload.pdf`
- **Page Count**: 350 pages
- **Pipeline Segments**: 310 Text Chunks, 0 Table Chunks
- **Total Propositions Extracted**: 2,791
- **Total End-to-End Pipeline Time**: 1829.150s (~30.5 minutes)

## 2. Infrastructure Configuration (Baseline)
- **Modal Containers**: 15 Total (5 Text Workers, 5 Neo4j Workers, 5 Qdrant Workers)
- **Hardware Profile**: Default Modal Container (`cpu=1.0`)
- **Total Compute Allocated**: 15 CPU Cores

## 3. Phase Breakdown & Log Analysis

### Phase 1: Document Extraction
- **Total Time**: 676.710s (~11.2 minutes)
- **Bottleneck Analysis**:
  The extraction phase actually completed processing the chunks in under 60 seconds. However, due to the rapid completion, all 5 text workers submitted their final 9 chunk batches at the exact same millisecond. This triggered a **429 Too Many Requests** rate-limit error from the free Ngrok tunnel. The workers silently swallowed the error, resulting in the server waiting roughly 10 minutes (until lease timeout) for the missing chunks to be acknowledged.
  *Note: A retry mechanism has since been patched into `text_workers.py` to prevent this.*

### Phase 2: Qdrant Ingestion (Vector Search)
- **Total Time**: 767.470s (~12.7 minutes)
- **Workload**: Calculating 768-dimensional dense embeddings for 2,791 individual sentences using `nomic-embed-text-v1.5`.
- **Bottleneck Analysis**:
  Because each of the 5 Qdrant workers only possessed 1 CPU core, they processed roughly 558 sentences each on a single virtual CPU. Transformer embedding models are extremely math-heavy, resulting in roughly 1.3 seconds of processing time per embedding.

### Phase 2: Neo4j Ingestion (Knowledge Graph)
- **Total Time**: 1149.310s (~19.1 minutes)
- **Workload**: Performing Named Entity Recognition (NER) on 310 chunks using the heavy `GLiNER` (300M+ parameters) transformer model.
- **Bottleneck Analysis**:
  Neo4j workers were the slowest segment of the entire pipeline. The GLiNER model requires significant compute. Dividing 310 chunks across 5 single-core workers means each 1-core worker processed 62 chunks. The raw calculation speed equated to roughly 18.5 seconds per chunk, strictly limited by the single CPU thread.

## 4. Total Pipeline Output Metrics
- **Coverage**: 91.6%
- **Knowledge Graph Components**: 
  - Mentions: 7,595
  - Specs: 1,050
- **Total Vectors Stored**: 310 (avg 283.4 words/chunk)

## 5. Architectural Upgrade (The 75-Core Fleet)
To drastically reduce the Phase 2 bottlenecks, the Modal architecture has been updated:
- **New Container Specs**: `cpu=5.0`
- **Total Fleet Compute**: 75 CPU Cores (15 containers × 5 cores)
- **Expected Results**: By granting 5 dedicated CPU threads to every single GLiNER and Nomic transformer model, processing times for the Neo4j and Qdrant stages should drop exponentially in the next run.
