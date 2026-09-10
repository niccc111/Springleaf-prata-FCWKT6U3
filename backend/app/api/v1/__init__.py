"""API v1 router assembly."""

from fastapi import APIRouter

from app.api.v1 import (
    alerts,
    audit,
    auth,
    optimise,
    orders,
    routes,
    system,
    uploads,
    users,
    vehicles,
)

api_router = APIRouter()
for module in (auth, orders, vehicles, routes, optimise, alerts, audit, uploads, users, system):
    api_router.include_router(module.router)

__all__ = ["api_router"]
