import os
import time
import json
import sqlite3
import subprocess
import signal
from pathlib import Path

DB_PATH = Path("storage/results.db")


def _results_count():
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, round_idx INTEGER, coef TEXT, intercept REAL, acc REAL, log_loss REAL, brier REAL, base_acc REAL, base_log_loss REAL, base_brier REAL)")
        cur.execute("SELECT COUNT(*) FROM results")
        n = cur.fetchone()[0]
        conn.close()
        return int(n)
    except Exception:
        return 0


def _kill(proc: subprocess.Popen):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/F", "/T"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        pass


def _spawn(cmd):
    if os.name == "nt":
        return subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    else:
        return subprocess.Popen(cmd, preexec_fn=os.setsid, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def bench_provider(python):
    before = _results_count()
    p = _spawn([python, "main.py", "--mode", "provider", "--node", "LAL", "--host", "127.0.0.1", "--port", "5400"])
    try:
        t0 = time.time()
        timeout = 60
        while time.time() - t0 < timeout:
            if _results_count() > before:
                break
            time.sleep(1)
        dur = time.time() - t0
        return dur
    finally:
        _kill(p)


def bench_p2p(python):
    before = _results_count()
    reporter = _spawn([python, "main.py", "--mode", "p2p", "--node", "MIA", "--host", "127.0.0.1", "--port", "5410", "--rounds", "1"]) 
    peer = _spawn([python, "main.py", "--mode", "p2p", "--node", "BOS", "--host", "127.0.0.1", "--port", "5411", "--peers", "MIA@127.0.0.1:5410", "--workers", "1"]) 
    try:
        t0 = time.time()
        timeout = 60
        while time.time() - t0 < timeout:
            if _results_count() > before:
                break
            time.sleep(1)
        dur = time.time() - t0
        return dur
    finally:
        _kill(peer)
        _kill(reporter)


def bench_gossip(python):
    before = _results_count()
    reporter = _spawn([python, "main.py", "--mode", "p2p-gossip", "--node", "MIA", "--host", "127.0.0.1", "--port", "5420", "--reporter", "--gossip-rounds", "1", "--peers", "BOS@127.0.0.1:5421,CHI@127.0.0.1:5422"]) 
    peer1 = _spawn([python, "main.py", "--mode", "p2p-gossip", "--node", "BOS", "--host", "127.0.0.1", "--port", "5421", "--peers", "MIA@127.0.0.1:5420", "--gossip-rounds", "1"]) 
    peer2 = _spawn([python, "main.py", "--mode", "p2p-gossip", "--node", "CHI", "--host", "127.0.0.1", "--port", "5422", "--peers", "MIA@127.0.0.1:5420", "--gossip-rounds", "1"]) 
    try:
        t0 = time.time()
        timeout = 60
        while time.time() - t0 < timeout:
            if _results_count() > before:
                break
            time.sleep(1)
        dur = time.time() - t0
        return dur
    finally:
        _kill(peer2)
        _kill(peer1)
        _kill(reporter)


def main():
    python = os.environ.get("PYTHON", str(Path(".venv")/"Scripts"/"python.exe" if os.name=="nt" else "python3"))
    results = {}
    print("[bench] provider...")
    results["provider_sec"] = bench_provider(python)
    print("[bench] p2p...")
    results["p2p_sec"] = bench_p2p(python)
    print("[bench] gossip...")
    results["gossip_sec"] = bench_gossip(python)

    Path("storage").mkdir(exist_ok=True)
    with open("storage/bench.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print("[bench] saved storage/bench.json:", results)


if __name__ == "__main__":
    main()
