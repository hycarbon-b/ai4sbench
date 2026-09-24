from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import time
import uuid

from sqlalchemy import select

from .config import Settings, get_settings
from .database import Base, create_database_engine, create_session_factory
from .deliveries import claim_delivery, complete_delivery, fail_delivery, send_delivery
from .job_queue import claim, complete, defer, fail
from .models import DatabaseJob, OutboundDelivery
from .providers import EC2Provider, provider_from_settings
from .services import (
    CapacityError,
    begin_immediate,
    handle_launch,
    handle_terminate,
    mark_job_exhausted,
    reconcile,
    record_job_attempt_failure,
)

logger = logging.getLogger("ai4sbench.jobs")


class JobRunner:
    def __init__(self, settings: Settings, provider: EC2Provider | None = None) -> None:
        self.settings = settings
        self.engine = create_database_engine(settings)
        self.sessions = create_session_factory(self.engine)
        self.provider = provider or provider_from_settings(settings)
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.stopping = False
        self.initialized = False

    async def initialize(self) -> None:
        if self.initialized:
            return
        if self.settings.auto_create_schema:
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        self.initialized = True

    def request_stop(self, *_args: object) -> None:
        self.stopping = True

    async def process_one(self) -> bool:
        async with self.sessions() as session:
            await begin_immediate(session)
            job = await claim(session, self.owner, self.settings.job_lease_seconds)
            await session.commit()
        if job is None:
            return False

        try:
            run_id = str(job.payload["run_id"])
            async with self.sessions() as session:
                if job.kind == "launch_run":
                    await handle_launch(session, run_id, self.provider, self.settings)
                elif job.kind == "terminate_run":
                    await handle_terminate(session, run_id, self.provider)
                else:
                    raise ValueError(f"Unsupported job kind: {job.kind}")
            async with self.sessions() as session:
                await complete(session, job.id, self.owner)
                await session.commit()
            logger.info("completed job=%s kind=%s", job.id, job.kind)
        except CapacityError:
            async with self.sessions() as session:
                current = await session.get(DatabaseJob, job.id)
                if current is not None:
                    await defer(session, current, self.owner)
                    await session.commit()
            logger.info("deferred job=%s because worker capacity is full", job.id)
        except Exception as exc:
            logger.exception("failed job=%s kind=%s", job.id, job.kind)
            terminal = False
            error = f"{type(exc).__name__}: {exc}"
            async with self.sessions() as session:
                current = await session.scalar(select(DatabaseJob).where(DatabaseJob.id == job.id))
                if current is not None:
                    terminal = current.attempts >= current.max_attempts
                    await fail(session, current, self.owner, error)
                    await session.commit()
            if job.kind in {"launch_run", "terminate_run"}:
                async with self.sessions() as session:
                    current = await session.get(DatabaseJob, job.id)
                    if current is not None:
                        await record_job_attempt_failure(session, current, error)
            if terminal:
                async with self.sessions() as session:
                    current = await session.get(DatabaseJob, job.id)
                    if current is not None:
                        await mark_job_exhausted(session, current)
        return True

    async def process_delivery_one(self) -> bool:
        async with self.sessions() as session:
            delivery = await claim_delivery(session)
            await session.commit()
        if delivery is None:
            return False

        try:
            status_code, body, message_url = await send_delivery(delivery, self.settings)
            async with self.sessions() as session:
                await complete_delivery(session, delivery, status_code, body, message_url)
                await session.commit()
            logger.info(
                "completed delivery=%s type=%s event=%s",
                delivery.id,
                delivery.delivery_type,
                delivery.event_type,
            )
        except Exception as exc:
            logger.exception(
                "failed delivery=%s type=%s event=%s",
                delivery.id,
                delivery.delivery_type,
                delivery.event_type,
            )
            async with self.sessions() as session:
                current = await session.get(OutboundDelivery, delivery.id)
                if current is not None:
                    await fail_delivery(
                        session,
                        current,
                        f"{type(exc).__name__}: {exc}",
                        response_status=getattr(exc, "status_code", None),
                        response_body=getattr(exc, "response_body", None),
                    )
                    await session.commit()
        return True

    async def run_once(self) -> bool:
        await self.initialize()
        async with self.sessions() as session:
            await reconcile(session)
        processed_job = await self.process_one()
        processed_delivery = await self.process_delivery_one()
        return processed_job or processed_delivery

    async def run_forever(self) -> None:
        await self.initialize()
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        last_reconcile = 0.0
        logger.info("database job runner started owner=%s", self.owner)
        while not self.stopping:
            now = time.monotonic()
            if now - last_reconcile >= 15:
                async with self.sessions() as session:
                    reconciled = await reconcile(session)
                if reconciled:
                    logger.warning("timed out runs=%s", reconciled)
                last_reconcile = now
            processed_job = await self.process_one()
            processed_delivery = await self.process_delivery_one()
            if not processed_job and not processed_delivery:
                await asyncio.sleep(self.settings.queue_poll_seconds)
        await self.engine.dispose()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Process ai4sbench SQLite jobs")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner = JobRunner(get_settings())
    if args.once:

        async def run_once() -> None:
            try:
                await runner.run_once()
            finally:
                await runner.engine.dispose()

        asyncio.run(run_once())
    else:
        asyncio.run(runner.run_forever())


if __name__ == "__main__":
    run()
