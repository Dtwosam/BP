import argparse
import asyncio
import json

from bp_engine.backfill.live_smoke import run_live_source_smoke


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test BP historical public data sources")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="fail unless every source, including Bybit, is reachable and non-empty",
    )
    args = parser.parse_args()
    report = asyncio.run(run_live_source_smoke())
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.require_all and report["status"] != "ok":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
