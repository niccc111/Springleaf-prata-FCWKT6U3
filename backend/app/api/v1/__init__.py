"""API v1 router assembly."""

from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    audit,
    optimise,
    orders,
    routes,
    system,
    uploads,
    vehicles,
)

api_router = APIRouter()
for module in (orders, vehicles, routes, optimise, alerts, audit, uploads, system):
    api_router.include_router(module.router)

__all__ = ["api_router"]
