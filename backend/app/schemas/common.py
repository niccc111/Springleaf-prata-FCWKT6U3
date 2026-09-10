"""Shared response envelopes and pagination helpers."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class FieldError(BaseModel):
    field: str
    message: str


class ErrorResponse(BaseModel):
    error: str
    message: str
    fields: list[FieldError] = Field(default_factory=list)
    details: dict = Field(default_factory=dict)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class MessageResponse(BaseModel):
    message: str
