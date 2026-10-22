"""Run the full pipeline end to end.

Usage:  python -I pipeline/run_all.py            (re-uses data already in data/raw)
        python -I pipeline/run_all.py --fetch    (re-downloads every source first)
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["normalize_queues.py", "prepare_eia.py", "build_network.py", "match_queue.py", "build_model.py",
         "export_map.py", "claims.py"]

if __name__ == "__main__":
    steps = (["fetch_data.py"] if "--fetch" in sys.argv else []) + STEPS
    for s in steps:
        t = time.time()
        print(f"\n=== {s}", flush=True)
        subprocess.run([sys.executable, "-I", "-u", str(HERE / s)], check=True, cwd=HERE.parent)
        print(f"=== {s} done in {time.time() - t:.0f}s", flush=True)
