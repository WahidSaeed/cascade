"""`cascade-ingest` worker (BUILD.md §9.2, §9.8): long-polls SQS and ingests each document.

Locally (no CASCADE_INGEST_QUEUE_URL) ingestion runs on an in-process background thread instead, and
this process just idles so docker-compose's `ingest-worker` service stays up.
A message that keeps failing is left for SQS to move to the dead-letter queue after its max receives.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import db
from ..config import settings
from .ingest import ingest

log = logging.getLogger("cascade.ingest.worker")


def handle(body: str) -> None:
    msg = json.loads(body)
    ingest(str(msg["document_id"]))


def poll_once(sqs: Any, queue_url: str, wait_s: int = 20) -> int:
    resp = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=5, WaitTimeSeconds=wait_s,
                               VisibilityTimeout=300)
    done = 0
    for m in resp.get("Messages", []):
        try:
            handle(m["Body"])
        except Exception:  # noqa: BLE001 - leave it on the queue; SQS redrives to the DLQ
            log.exception("ingestion failed; message will be retried")
            continue
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])
        done += 1
    return done


def main() -> None:  # pragma: no cover - process entry point
    logging.basicConfig(level=logging.INFO)
    db.migrate()
    if not settings.ingest_queue_url:
        log.info("no CASCADE_INGEST_QUEUE_URL: ingestion runs in the API process; idling")
        while True:
            time.sleep(3600)
    import boto3

    sqs = boto3.client("sqs", region_name=settings.bedrock_region)
    while True:
        poll_once(sqs, settings.ingest_queue_url)


if __name__ == "__main__":
    main()
