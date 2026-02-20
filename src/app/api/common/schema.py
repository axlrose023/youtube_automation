from pydantic import BaseModel, Field, computed_field

from app.settings import get_config

config = get_config()


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(
        config.api.page_default_size, ge=1, le=config.api.page_max_size
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Pagination[T: BaseModel](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int

    @computed_field
    @property
    def total_pages(self) -> int:
        return (self.total + self.page_size - 1) // self.page_size

    @computed_field
    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @computed_field
    @property
    def has_prev(self) -> bool:
        return self.page > 1
