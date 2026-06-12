"""Pagination utilities with type safety."""

from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    """Query parameters for paginated requests with validation."""

    page: int = Field(default=1, ge=1, le=1000, description="Page number (1-indexed)")
    per_page: int = Field(default=10, ge=1, le=100, description="Items per page")
    with_total: bool = Field(
        default=False,
        description=(
            "Compute the total row count (and total_pages). Off by default to "
            "avoid a COUNT(*) on every list call (ISSUE-016); request it only "
            "when a page-number UI needs the total."
        ),
    )

    @property
    def offset(self) -> int:
        """Calculate SQL offset from page number."""
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """SQL limit value (alias for per_page)."""
        return self.per_page

    @property
    def fetch_limit(self) -> int:
        """Rows to SELECT for a page: one extra sentinel to detect has_next.

        Fetching ``per_page + 1`` rows lets the caller decide has_next from
        whether the sentinel came back, so no separate COUNT(*) is needed and
        the result is exact even when the final page is exactly full.
        """
        return self.per_page + 1


class PaginationMetadata(BaseModel):
    """Metadata about pagination state."""

    page: int
    per_page: int
    total: int | None = None
    total_pages: int | None = None
    has_next: bool
    has_prev: bool


class PaginatedResponse[T](BaseModel):
    """Standard paginated response wrapper with metadata."""

    items: list[T]
    metadata: PaginationMetadata

    @classmethod
    def create(
        cls,
        items: list[T],
        total: int | None,
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
        if total is not None:
            total_pages = max(1, (total + params.per_page - 1) // params.per_page)
            has_next = params.page < total_pages
        else:
            total_pages = None
            has_next = len(items) == params.per_page

        metadata = PaginationMetadata(
            page=params.page,
            per_page=params.per_page,
            total=total,
            total_pages=total_pages,
            has_next=has_next,
            has_prev=params.page > 1,
        )

        return cls(items=items, metadata=metadata)

    @classmethod
    def create_from_window(
        cls,
        rows: list[T],
        params: PaginationParams,
        total: int | None = None,
    ) -> "PaginatedResponse[T]":
        """Build a response from an over-fetched window of ``per_page + 1`` rows.

        ``rows`` is expected to have been fetched with ``params.fetch_limit``.
        If a sentinel (extra) row is present there is a next page; it is
        trimmed off the returned items. This yields an exact ``has_next``
        without a COUNT(*), even when the last page is exactly full.

        ``total`` is passed through only when the caller opted into counting
        (``params.with_total``); otherwise total / total_pages stay null.
        """
        has_next = len(rows) > params.per_page
        items = list(rows[: params.per_page])

        if total is not None:
            total_pages = max(1, (total + params.per_page - 1) // params.per_page)
        else:
            total_pages = None

        metadata = PaginationMetadata(
            page=params.page,
            per_page=params.per_page,
            total=total,
            total_pages=total_pages,
            has_next=has_next,
            has_prev=params.page > 1,
        )

        return cls(items=items, metadata=metadata)
