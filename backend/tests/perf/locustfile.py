"""Load test (BUILD.md §13, P7-04): a mixed demo workload at a constant ~50 requests/s.

Run by tests/perf/test_load.py:  locust -f locustfile.py --headless -u 50 -r 50 -t 60s --host <api>
Each of the 50 users sends one request per second (constant throughput), so the target rate is 50 rps.
"""
import random

from locust import HttpUser, constant_throughput, task

EXTRACT = ("Extract the order number, email and amount: 'Order NP-{n} for {u}@example.com was charged "
           "€{a}.90 twice'")
OPEN = ["Draft a friendly reply to a customer whose {p} arrived late", "Write a product description for the {p}",
        "Write a short Instagram caption for our new {p}"]
PRODUCTS = ["tent", "backpack", "headlamp", "rain shell", "fleece", "camp stove"]
UNITS = [("km", "miles"), ("kg", "lb"), ("m", "feet")]


class CascadeUser(HttpUser):
    wait_time = constant_throughput(1)

    def on_start(self) -> None:
        self.user = f"user{random.randint(1, 20):02d}"

    def _ask(self, prompt: str, name: str) -> None:
        with self.client.post("/api/chat", json={"prompt": prompt}, headers={"X-User-Id": self.user}, name=name,
                              catch_response=True) as r:
            if r.status_code != 200 or not r.json().get("answer"):
                r.failure(f"status {r.status_code}")

    @task(4)
    def extraction(self) -> None:
        self._ask(EXTRACT.format(n=random.randint(10000, 99999), u=random.choice(["jane", "omar", "li"]),
                                 a=random.randint(5, 400)), "artifact: extraction")

    @task(2)
    def conversion(self) -> None:
        u1, u2 = random.choice(UNITS)
        self._ask(f"Convert {random.randint(1, 500)} {u1} to {u2}", "artifact: conversion")

    @task(3)
    def open_ended(self) -> None:
        self._ask(random.choice(OPEN).format(p=random.choice(PRODUCTS)), "llm or cache: open-ended")
