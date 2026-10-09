"""crm-mock: a stand-in for an existing CRM ("CRM tier lookup", S9). Fictional data only."""
from __future__ import annotations

from fastapi import FastAPI

from .demo_world import CRM_TIERS

app = FastAPI(title="Northpeak CRM (mock)")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/customers/tier")
def tier(email: str) -> dict[str, str]:
    return {"email": email.lower(), "tier": CRM_TIERS.get(email.lower(), "standard")}
