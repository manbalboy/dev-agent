"""FastAPI dependency helpers shared by routers."""

from __future__ import annotations

from fastapi import Request

from app.config import AppSettings
from app.store import JobStore



def get_settings(request: Request) -> AppSettings:
    """Expose AppSettings stored in FastAPI app state."""

    return request.app.state.settings



def get_store(request: Request) -> JobStore:
    """Expose JobStore stored in FastAPI app state."""

    return request.app.state.store
