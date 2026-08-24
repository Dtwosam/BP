import asyncio
import json

from bp_engine.backfill.live_smoke import run_live_source_smoke

if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_live_source_smoke()), indent=2, sort_keys=True))
