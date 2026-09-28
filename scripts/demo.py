#!/usr/bin/env python3
"""Cold-start local demo — no Vercel CLI, no server, just Python.

Usage:
    python scripts/demo.py CUST0001
    python scripts/demo.py CUST0001 --model-b anthropic/claude-opus-5.5 --threshold 0.9
    python scripts/demo.py --list          # see available sample customer ids
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from src import data_access, pipeline  # noqa: E402
from src.settings import ALLOWED_MODELS, PipelineSettings  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("customer_id", nargs="?")
    parser.add_argument("--list", action="store_true", help="List sample customer ids and exit.")
    parser.add_argument("--model-a", choices=ALLOWED_MODELS, default=None)
    parser.add_argument("--model-b", choices=ALLOWED_MODELS, default=None)
    parser.add_argument("--threshold", type=float, default=None, help="Evaluator approval threshold, 0-1.")
    parser.add_argument("--no-critic", action="store_true", help="Disable the evaluator-optimizer loop.")
    parser.add_argument("--max-revisions", type=int, default=None)
    args = parser.parse_args()

    if args.list:
        for row in data_access.list_customer_ids():
            print(row["customer_id"], row["city_tier"])
        return

    if not args.customer_id:
        parser.print_help()
        sys.exit(1)

    defaults = PipelineSettings()
    settings = PipelineSettings(
        model_a=args.model_a or defaults.model_a,
        model_b=args.model_b or defaults.model_b,
        evaluator_threshold=args.threshold if args.threshold is not None else defaults.evaluator_threshold,
        critic_enabled=not args.no_critic,
        max_revision_rounds=args.max_revisions if args.max_revisions is not None else defaults.max_revision_rounds,
    )
    result = pipeline.run(args.customer_id, settings)
    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
