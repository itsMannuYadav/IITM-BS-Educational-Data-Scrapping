from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class ResourceType(str, Enum):
    NOTES = "notes"
    CHEAT_SHEET = "cheat_sheet"
    PREVIOUS_PAPER = "previous_paper"
    SYLLABUS = "syllabus"
    LECTURE = "lecture"
    BOOK = "book"
    GDRIVE = "gdrive"
    LINK = "link"
    OTHER = "other"


class ResourceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    source: str
    title: str
    course: str | None = None
    term: str | None = None
    program: str | None = None  # e.g. DS / ES / Foundation / Diploma
    type: ResourceType = ResourceType.OTHER
    authors: list[str] = Field(default_factory=list)
    original_url: str
    file_url: str | None = None
    local_path: str | None = None
    mime_or_ext: str | None = None
    gdrive_urls: list[str] = Field(default_factory=list)
    permission_note: str = "written-approval-from-source"
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_index_row(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
