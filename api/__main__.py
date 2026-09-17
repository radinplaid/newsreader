"""Run the newsreader server: python -m api"""
from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="newsreader", description="Run the newsreader server")
    parser.add_argument("--host", default=os.environ.get("NR_HOST", "127.0.0.1"),
                        help="bind address (default: NR_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("NR_PORT", "8000")),
                        help="bind port (default: NR_PORT or 8000)")
    parser.add_argument("--log-level", default=os.environ.get("NR_LOG_LEVEL", "info"),
                        help="uvicorn log level (default: NR_LOG_LEVEL or info)")
    args = parser.parse_args()
    uvicorn.run("api.app:app", host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
