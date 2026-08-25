from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import time
import uuid

from sqlalchemy import select

from .config import Settings, get_settings
from .database import Base, create_database_engine, create_session_factory
from .job_queue import claim, complete, defer, fail
from .models import DatabaseJob
from .providers import EC2Provider, provider_from_settings
from .services import (
    CapacityError,
    begin_immediate,
    handle_launch,
    handle_terminate,
    mark_job_exhausted,
    reconcile,
)

logger = logging.getLogger("ai4sbench.jobs")


class JobRunner:
    def __init__(self, settings: Settings, provider: EC2Provider | None = None) -> None:
        self.settings = settings
        self.engine = create_database_engine(settings)
        if settings.auto_create_schema:
            Base.metadata.create_all(self.engine)
        self.sessions = create_session_factory(self.engine)
        self.provider = provider or provider_from_settings(settings)
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.stopping = False

    def request_stop(self, *_args: object) -> None:
        self.stopping = True

    def process_one(self) -> bool:
        with self.sessions() as session:
            begin_immediate(session)
            job = claim(session, self.owner, self.settings.job_lease_seconds)
            session.commit()
        if job is None:
            return False

        try:
            run_id = str(job.payload["run_id"])
            with self.sessions() as session:
                if job.kind == "launch_run":
                    handle_launch(session, run_id, self.provider, self.settings)
                elif job.kind == "terminate_run":
                    handle_terminate(session, run_id, self.provider)
                else:
                    raise ValueError(f"Unsupported job kind: {job.kind}")
            with self.sessions() as session:
                complete(session, job.id, self.owner)
                session.commit()
            logger.info("completed job=%s kind=%s", job.id, job.kind)
        except CapacityError:
            with self.sessions() as session:
                current = session.get(DatabaseJob, job.id)
                if current is not None:
                    defer(session, current, self.owner)
                    session.commit()
            logger.info("deferred job=%s because worker capacity is full", job.id)
        except Exception as exc:
            logger.exception("failed job=%s kind=%s", job.id, job.kind)
            terminal = False
            with self.sessions() as session:
                current = session.scalar(select(DatabaseJob).where(DatabaseJob.id == job.id))
                if current is not None:
                    terminal = current.attempts >= current.max_attempts
                    fail(session, current, self.owner, f"{type(exc).__name__}: {exc}")
                    session.commit()
            if terminal:
                with self.sessions() as session:
                    current = session.get(DatabaseJob, job.id)
                    if current is not None:
                        mark_job_exhausted(session, current)
        return True

    def run_once(self) -> bool:
        with self.sessions() as session:
            reconcile(session)
        return self.process_one()

    def run_forever(self) -> None:
        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)
        last_reconcile = 0.0
        logger.info("database job runner started owner=%s", self.owner)
        while not self.stopping:
            now = time.monotonic()
            if now - last_reconcile >= 15:
                with self.sessions() as session:
                    reconciled = reconcile(session)
                if reconciled:
                    logger.warning("timed out runs=%s", reconciled)
                last_reconcile = now
            if not self.process_one():
                time.sleep(self.settings.queue_poll_seconds)
        self.engine.dispose()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Process ai4sbench SQLite jobs")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    runner = JobRunner(get_settings())
    if args.once:
        runner.run_once()
        runner.engine.dispose()
    else:
        runner.run_forever()


if __name__ == "__main__":
    run()
