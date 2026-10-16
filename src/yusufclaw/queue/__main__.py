"""Local operator CLI: python -m yusufclaw.queue [list|show|events|cancel]."""

import argparse
import asyncio
import json
from typing import Any

from yusufclaw.queue import default_queue


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "show", "events", "cancel"])
    parser.add_argument("job_id", nargs="?")
    args = parser.parse_args()
    if args.action != "list" and not args.job_id:
        parser.error("job_id is required")
    result: Any
    queue = default_queue()
    try:
        await queue.initialize()
        if args.action == "list":
            result = [j.model_dump(exclude={"payload", "result"}) for j in await queue.list()]
        elif args.action == "show":
            result = (await queue.get(args.job_id)).model_dump()
        elif args.action == "events":
            result = await queue.events(args.job_id)
        else:
            await queue.cancel(args.job_id)
            result = {"cancelled": args.job_id}
        print(json.dumps(result, indent=2))
    finally:
        await queue.close()


if __name__ == "__main__":
    asyncio.run(main())
