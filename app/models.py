from datetime import datetime, timezone
from sqlalchemy import BigInteger, DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    short_id: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    source_url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader: Mapped[str | None] = mapped_column(String(255), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    video_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    transcoded: Mapped[str | None] = mapped_column(String(10), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=lambda: datetime.now(timezone.utc)
    )
