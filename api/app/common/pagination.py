"""Pagination utilities with type safety."""
from typing import Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class PaginationParams(BaseModel):
    """Query parameters for paginated requests with validation."""

    page: int = Field(default=1, ge=1, description="Page number (1-indexed)")
    per_page: int = Field(default=10, ge=1, le=100, description="Items per page")

    @property
    def offset(self) -> int:
        """Calculate SQL offset from page number."""
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """SQL limit value (alias for per_page)."""
        return self.per_page


class PaginationMetadata(BaseModel):
    """Metadata about pagination state."""

    page: int
    per_page: int
    total: int
    total_pages: int
    has_next: bool
    has_prev: bool


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard paginated response wrapper with metadata."""

    items: list[T]
    metadata: PaginationMetadata

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int,
        params: PaginationParams,
    ) -> "PaginatedResponse[T]":
        """
        Build a paginated response from query results and params.
        
        Args:
            items: List of items for current page
            total: Total number of items across all pages
            params: Pagination parameters
            
        Returns:
            PaginatedResponse with items and metadata
        """
        total_pages = max(1, (total + params.per_page - 1) // params.per_page)
        
        metadata = PaginationMetadata(
            page=params.page,
            per_page=params.per_page,
            total=total,
            total_pages=total_pages,
            has_next=params.page < total_pages,
            has_prev=params.page > 1,
        )
        
        return cls(items=items, metadata=metadata)