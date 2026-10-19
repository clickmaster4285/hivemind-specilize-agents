#!/usr/bin/env python3
"""CLI entrypoint: start the API (with its in-process worker) or a standalone worker."""

from __future__ import annotations

import argparse


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the MulteAgent API or a standalone worker.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--gui",
        action="store_true",
        help="Launch the Python API (jobs + auth + in-process worker)",
    )
    group.add_argument(
        "--worker",
        action="store_true",
        help="Run a standalone horizontal worker process",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.gui:
        from src.config import Config

        cfg = Config.from_env()
        print(f"Starting MulteAgent API at http://{cfg.api_host}:{cfg.api_port}")
        print("In another terminal: cd frontend && npm run dev")
        print("Default login: admin / admin123")
        from web.server import main as gui_main

        gui_main()
        return 0

    from worker_main import main as worker_main

    worker_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
