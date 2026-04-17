#!/usr/bin/env python3
"""
Full pipeline runner. Each stage checkpoints its output and is skipped if
that output already exists — unless a --force flag is passed.

Stages:
  1. build_dessertperson_graph   → data/derived/dessertperson_graph.pkl
  2. build_recipe_db             → data/derived/matrix_data.json
                                   data/derived/recipes_db.json
  3. attach_reddit               → updates recipes_db.json

Usage:
  conda run -n saffitz python run_pipeline.py          # run missing stages only
  conda run -n saffitz python run_pipeline.py --force  # re-run everything
  conda run -n saffitz python run_pipeline.py --force-matrix   # re-extract matrix
  conda run -n saffitz python run_pipeline.py --stage 3        # single stage
"""

import argparse
import subprocess
import sys
from pathlib import Path

from persist import GRAPH_PATH, MATRIX_DATA_PATH, RECIPES_DB_PATH


def run(cmd: list[str]):
    print(f"\n{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    print('='*60)
    result = subprocess.run(cmd, check=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force",        action="store_true", help="Re-run all stages")
    parser.add_argument("--force-matrix", action="store_true", help="Re-extract matrix data")
    parser.add_argument("--stage",        type=int, choices=[1, 2, 3], help="Run only this stage")
    args = parser.parse_args()

    python = sys.executable

    stages_to_run = {args.stage} if args.stage else {1, 2, 3}

    # Stage 1: build Reddit graph
    if 1 in stages_to_run:
        if args.force or not GRAPH_PATH.exists():
            run([python, "build_dessertperson_graph.py"])
        else:
            print(f"Stage 1 skipped: {GRAPH_PATH.name} exists")

    # Stage 2: build recipe DB from TOC + matrix
    if 2 in stages_to_run:
        needs_run = args.force or not RECIPES_DB_PATH.exists()
        extra = ["--force-matrix"] if args.force or args.force_matrix else []
        if needs_run or args.force_matrix:
            run([python, "build_recipe_db.py"] + extra)
        else:
            print(f"Stage 2 skipped: {RECIPES_DB_PATH.name} exists")

    # Stage 3: attach Reddit data
    if 3 in stages_to_run:
        extra = ["--force"] if args.force else []
        run([python, "attach_reddit.py"] + extra)


if __name__ == "__main__":
    main()
