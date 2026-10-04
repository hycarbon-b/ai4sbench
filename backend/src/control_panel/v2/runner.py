from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress

from src.control_panel.v2.service import HarborV2Runner, validate_ready
from src.core.config import get_settings
from src.db.database import Base, create_database_engine, create_session_factory


async def serve(*, once: bool = False) -> None:
    settings = get_settings()
    validate_ready(settings, require_local_runtime=True)
    engine = create_database_engine(settings)
    sessions = create_session_factory(engine)
    if settings.auto_create_schema:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    runner = HarborV2Runner(settings, sessions)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(name, stop.set)
    try:
        while not stop.is_set():
            processed = await runner.run_once()
            if once:
                break
            if not processed:
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=settings.queue_poll_seconds)
    finally:
        await engine.dispose()


def run() -> None:
    parser = argparse.ArgumentParser(description="Run Harbor CLI managed EC2 jobs")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(once=args.once))


if __name__ == "__main__":
    run()
