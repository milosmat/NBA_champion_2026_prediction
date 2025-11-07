# NBA Champion 2026 – Federated Learning & Actor System

Distributed system for predicting the NBA champion (and individual playoff series) using federated learning (FedAvg / FedProx) on historical NBA data. Implemented with a custom asynchronous actor model supporting multiple orchestration modes: provider (central aggregator), peer‑to‑peer (P2P), and gossip. Includes gRPC transport, CRDT replication, and playoff bracket simulation (QF → SF → F) with persistence to SQLite.

## Contents

1. Features  
2. Architecture (actors & messages)  
3. Requirements & installation  
4. Data preparation  
5. Running (provider / p2p / p2p-gossip)  
6. gRPC transport (stubs, run, troubleshooting)  
7. Federated algorithms (FedAvg, FedProx)  
8. Playoff simulation & champion extraction  
9. CRDT (PN-Counter, LWW-Map)  
10. Benchmarking  
11. Testing (pytest)  
12. SQLite schema  
13. Troubleshooting & FAQ  
14. Next steps / ideas  

## 1. Features

- Federated learning: FedAvg + optional FedProx (μ regularization on client with server blend).
- Multiple orchestration modes: central provider, P2P work‑stealing, gossip round synchronization (barrier via `PeerReady`).
- Evaluator: accuracy, log_loss, brier; baseline centralized model; playoff series simulation & bracket resolution.
- CRDT: PN-Counter (round counting) and LWW-Map (example key/value replication) with delta replicator.
- Health & supervision: ping/ack, restart logic.
- gRPC transport (in addition to raw TCP) via generated protobuf stubs.
- SQLite persistence: per‑round results + playoff series (stage: QF/SF/F) + global model JSON.
- Scripts: benchmarking, playoff champion extraction.

## 2. Architecture

Asyncio actors:

- `TeamNodeWorker` / `TeamNodeP2P`: local training, sending model updates.
- `Aggregator` (provider) / `AggregatorP2P`: collecting updates & performing FedAvg / broadcasting global model.
- `Evaluator`: metrics + playoff bracket simulation.
- `Scheduler` (provider mode): task assignment / work stealing.
- `CrdtReplicator`: CRDT delta dissemination.
- `HealthMonitor` & `Supervisor`: liveness checks and restart orchestration.

Representative messages:  
`TrainRequest`, `ModelShare`, `RoundComplete`, `SetGlobalModel`, `EvalRequest`, `EvalReport`, `PeerList`, `PeerReady`, `StartRound`, `CrdtDelta`, `HealthPing`, `HealthAck`.

## 3. Requirements & Installation

Recommended: Python 3.11+ (3.13 also fine). PowerShell examples below (Windows).

Create and activate virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip setuptools wheel
pip install scikit-learn numpy pandas grpcio grpcio-tools
```

Generate gRPC stubs:

```powershell
python -m grpc_tools.protoc -I rpc --python_out=rpc --grpc_python_out=rpc rpc/actor.proto
```

## 4. Data Preparation

Directory `dataset/` already contains cleaned CSV (`nba_games_clean.csv`) and team CSV files under `dataset/teams` or `teams/`. Code reads them directly—no extra import steps. If adding new CSVs, keep column names consistent with existing schema.

## 5. Running Modes

Entry point: `main.py` with `--mode`.

Common arguments:

| Flag | Meaning |
|------|---------|
| `--mode` | provider | p2p | p2p-gossip |
| `--node` | node identifier (e.g. MIA) |
| `--host` / `--port` | networking parameters |
| `--transport` | tcp | grpc |
| `--rounds` | number of federated rounds (provider / p2p) |
| `--fedprox_mu` | FedProx μ coefficient |
| `--async-fed` | asynchronous federated learning (no round barrier; P2P with Scheduler/Worker) |
| `--async-batch` | number of `ModelShare` updates per async aggregation (default 8) |

### 5.1 Provider Mode

Central scheduler + aggregator + evaluator.

```powershell
python main.py --mode provider --node HUB --host 127.0.0.1 --port 5000 --rounds 2
```

Workers (auto‑register / work stealing):

```powershell
python main.py --mode provider --node W1 --host 127.0.0.1 --port 5001 --peers HUB@127.0.0.1:5000
python main.py --mode provider --node W2 --host 127.0.0.1 --port 5002 --peers HUB@127.0.0.1:5000
```

### 5.2 P2P Mode

Each node trains locally and shares its model; aggregator role is peer-level.

```powershell
python main.py --mode p2p --node MIA --host 127.0.0.1 --port 5100 --rounds 2
python main.py --mode p2p --node BOS --host 127.0.0.1 --port 5101 --peers MIA@127.0.0.1:5100 --rounds 2
python main.py --mode p2p --node CHI --host 127.0.0.1 --port 5102 --peers MIA@127.0.0.1:5100,BOS@127.0.0.1:5101 --rounds 2
```

#### 5.2.1 P2P Async (No Barrier)

Async mode (P2P with Scheduler/Worker) ignores `--rounds`; global models broadcast incrementally after each `--async-batch` local update group.

```powershell
python main.py --mode p2p --node MIA --host 127.0.0.1 --port 5110 --async-fed --async-batch 8 --fedprox_mu 0.01
python main.py --mode p2p --node BOS --host 127.0.0.1 --port 5111 --peers MIA@127.0.0.1:5110 --async-fed --async-batch 8 --fedprox_mu 0.01
python main.py --mode p2p --node CHI --host 127.0.0.1 --port 5112 --peers MIA@127.0.0.1:5110 --async-fed --async-batch 8 --fedprox_mu 0.01
```

Stopping async P2P:
1. Switch to classic round mode (omit `--async-fed`, set `--rounds N`).
2. Use gossip async mode (5.3.1) with built‑in stop conditions (flush count, time, convergence). Similar stop flags can be added to P2P async if needed.

### 5.3 Gossip Mode

One reporter (aggregator) waits for all peers in each round; others just send local shares.

```powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5200 --peers BOS@127.0.0.1:5201,CHI@127.0.0.1:5202 --reporter --gossip-rounds 2 --gossip-eval
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5201 --peers MIA@127.0.0.1:5200,CHI@127.0.0.1:5202
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5202 --peers MIA@127.0.0.1:5200,BOS@127.0.0.1:5201
```

Round‑based; for fully asynchronous streaming use P2P async (5.2.1).

#### 5.3.1 Gossip Async (Continuous)

Experimental: continuous gossip mode (no barrier) with batching windows and staleness.

Parameters:

| Flag | Meaning |
|------|---------|
| `--gossip-async` | Enable continuous gossip |
| `--gossip-batch` | Min shares before reporter flush |
| `--gossip-window-ms` | Time window for flush if batch not reached |
| `--gossip-interval-ms` | Local train/share interval per peer |
| `--gossip-staleness` | α weight for staleness (higher = faster decay of old versions) |
| `--gossip-max-flushes` | Max flushes before auto stop (0 = no limit) |
| `--gossip-max-seconds` | Max duration (0 = no limit) |
| `--gossip-converge-eps` | Convergence epsilon (L2 coef delta + |intercept delta|) |
| `--gossip-converge-patience` | Consecutive flushes under eps to declare convergence |
| `--gossip-eval-on-stop` | Run playoff eval at stop (reporter only) |

Example start (continuous):

```powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5210 --peers BOS@127.0.0.1:5211,CHI@127.0.0.1:5212 --reporter --gossip-async --gossip-batch 4 --gossip-window-ms 1500 --gossip-interval-ms 2000 --gossip-staleness 0.5
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5211 --peers MIA@127.0.0.1:5210,CHI@127.0.0.1:5212 --gossip-async --gossip-interval-ms 2000
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5212 --peers MIA@127.0.0.1:5210,BOS@127.0.0.1:5211 --gossip-async --gossip-interval-ms 2000
```

Stop after fixed flush count:

```powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5220 --peers BOS@127.0.0.1:5221,CHI@127.0.0.1:5222 --reporter --gossip-async --gossip-batch 3 --gossip-max-flushes 5 --gossip-eval-on-stop
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5221 --peers MIA@127.0.0.1:5220,CHI@127.0.0.1:5222 --gossip-async
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5222 --peers MIA@127.0.0.1:5220,BOS@127.0.0.1:5221 --gossip-async
```

Stop on convergence or timeout:

```powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5230 --peers BOS@127.0.0.1:5231,CHI@127.0.0.1:5232 --reporter --gossip-async --gossip-batch 3 --gossip-converge-eps 0.001 --gossip-converge-patience 3 --gossip-max-seconds 60 --gossip-eval-on-stop
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5231 --peers MIA@127.0.0.1:5230,CHI@127.0.0.1:5232 --gossip-async
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5232 --peers MIA@127.0.0.1:5230,BOS@127.0.0.1:5231 --gossip-async
```

Notes:
- No `--gossip-rounds` in async gossip; evaluation triggered manually or via `--gossip-eval` time window.
- Results are stream‑like; metrics vary over time.
- Reporter stops automatically when any configured stop condition is met; peers also exit their actor loop.

### 5.4 gRPC Transport

Add `--transport grpc` to processes (after generating stubs). Ports unchanged.

```powershell
python main.py --mode p2p-gossip --node MIA --host 127.0.0.1 --port 5300 --peers BOS@127.0.0.1:5301,CHI@127.0.0.1:5302 --reporter --gossip-rounds 2 --gossip-eval --transport grpc
python main.py --mode p2p-gossip --node BOS --host 127.0.0.1 --port 5301 --peers MIA@127.0.0.1:5300,CHI@127.0.0.1:5302 --transport grpc
python main.py --mode p2p-gossip --node CHI --host 127.0.0.1 --port 5302 --peers MIA@127.0.0.1:5300,BOS@127.0.0.1:5301 --transport grpc
```

If stub errors appear, follow error instructions (usually regenerate protobuf stubs).

## 6. FedProx

Enable with `--fedprox-mu` (e.g. 0.01). Clients apply proximal regularization toward current global model.

```powershell
python main.py --mode p2p --node MIA --host 127.0.0.1 --port 5400 --rounds 3 --fedprox_mu 0.01
```

## 7. Playoff Simulation & Champion

- After final round (or when `--gossip-eval` triggers), evaluator simulates bracket: Quarterfinals (up to 16 teams) → Semifinals → Finals.
- Series recorded in `playoffs` table with `stage` (QF/SF/F).
- Champion extraction script:

```powershell
python scripts/who_wins_playoffs.py
```

Example output:

```
Finals: LBN vs UTH -> 4:3 winner=LBN ...
Predicted champion: LBN
```

## 8. CRDT

- PN-Counter: increments per round, replicated via deltas.
- LWW-Map: key→value with timestamp precedence.
- Log entries tagged `[CRDT]` show propagated state.

## 9. Benchmarking

`bench.py` runs scenarios and measures elapsed time until a new row appears in `results`.

```powershell
python bench.py
```

Output stored at `storage/bench.json`.

## 10. Tests

Run:

```powershell
pytest -q
```

Contains baseline tests for message serialization and FedAvg aggregation correctness.

## 11. SQLite Schema

File: `storage/results.db`

Tables:

- `results(id, timestamp, round_idx, acc, log_loss, brier, model_json)`
- `playoffs(id, round_idx, team_a, team_b, best_of, wins_a, wins_b, winner, stage, p_a_win, ts)`

Global model (sklearn coefficients) also stored in `global_model.json`.

## 12. Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| gRPC: "stubs not found" | Missing generated `actor_pb2*.py` | Run protoc command (section 3) |
| Import error `actor_pb2` | Missing `rpc/__init__.py` | Ensure `rpc/__init__.py` exists |
| No new playoff rows | Not final round / missing `--gossip-eval` | Run with `--gossip-eval` or complete rounds |
| FedProx no effect | μ=0 or no initial global model | Increase `--fedprox-mu` (>0), run more rounds |
| Port already in use | Previous process still running | Kill process / change port |
| Champion script empty | No finals recorded yet | Ensure evaluator ran, or trigger eval manually |

Champion check:

```powershell
python scripts/who_wins_playoffs.py
```

## 13. FAQ

| Question | Answer |
|----------|--------|
| Can I mix TCP and gRPC nodes? | Recommend uniform transport; mixing requires adapter logic. |
| Is async gossip reproducible? | Less deterministic—use fixed seeds & controlled intervals for experiments. |
| Why brier score? | Calibration measure—useful for probability quality beyond accuracy/log_loss. |
| Where are model weights stored? | `model_json` column (serialized coefficients/intercept) + `global_model.json`. |
| How to add a new team? | Append team CSV, ensure schema match, restart nodes (dynamic loading optional). |

## 14. Next Steps / Ideas

- Model personalization layer (team‑specific fine‑tuning after global broadcast).
- Differential privacy noise addition for model shares.
- Hierarchical federation (conference → league).
- Adaptive μ for FedProx based on divergence.
- Advanced CRDT (OR-Set for dynamic peer lists).
- Live WebSocket dashboard (metrics stream).
- GPU acceleration for training loops if model complexity increases.

---
