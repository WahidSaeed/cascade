"""The ingest worker consumes SQS messages (moto); failures stay on the queue for the DLQ."""
import json

import boto3
from moto import mock_aws

from app import seed
from app.config import settings
from app.rag import ingest, worker


def test_worker_ingests_from_sqs(fresh_db, monkeypatch):
    seed.seed_users()
    ingest.create_collection("Notes", "ava", "org", collection_id="col_notes")
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="cascade-ingest")["QueueUrl"]
        monkeypatch.setattr(settings, "ingest_queue_url", url)
        monkeypatch.setattr(ingest, "_pool", None)                       # must not run in-process
        doc = ingest.register("col_notes", "Tips", "tips.md", data=b"# Tips\n\nPitch tents on flat ground.\n")
        assert doc["status"] == "queued"
        sent = sqs.receive_message(QueueUrl=url, VisibilityTimeout=0)["Messages"][0]
        assert json.loads(sent["Body"]) == {"document_id": doc["id"]}
        assert worker.poll_once(sqs, url, wait_s=0) == 1
        assert ingest.get_document(doc["id"])["status"] == "ready"
        assert "Messages" not in sqs.receive_message(QueueUrl=url, WaitTimeSeconds=0)
        # a message that fails is not deleted (SQS will redrive it to the DLQ)
        sqs.send_message(QueueUrl=url, MessageBody=json.dumps({"document_id": "missing"}))
        assert worker.poll_once(sqs, url, wait_s=0) == 0
