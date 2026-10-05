"""Application settings loaded from environment."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Notion
    notion_token: str = Field(alias="NOTION_TOKEN")
    notion_database_id: str = Field(alias="NOTION_DATABASE_ID")
    notion_prop_status: str = Field(
        default="Estado de la publicación", alias="NOTION_PROP_STATUS"
    )
    notion_prop_publication: str = Field(default="Publicación", alias="NOTION_PROP_PUBLICATION")
    notion_prop_channel: str = Field(default="Canal ", alias="NOTION_PROP_CHANNEL")
    notion_prop_caption: str = Field(default="Caption", alias="NOTION_PROP_CAPTION")
    notion_prop_final_file: str = Field(default="Archivo Final", alias="NOTION_PROP_FINAL_FILE")
    notion_prop_title: str = Field(
        default="Titulo de la publicación ", alias="NOTION_PROP_TITLE"
    )
    notion_prop_content_type: str = Field(
        default="Tipo de contenido ", alias="NOTION_PROP_CONTENT_TYPE"
    )
    notion_prop_miniatura: str = Field(default="Miniatura", alias="NOTION_PROP_MINIATURA")
    notion_prop_protagonista: str = Field(
        default="Protagonista", alias="NOTION_PROP_PROTAGONISTA"
    )
    notion_status_approved: str = Field(
        default="Aprobado - Edición Final", alias="NOTION_STATUS_APPROVED"
    )
    notion_status_scheduled: str = Field(default="Programado", alias="NOTION_STATUS_SCHEDULED")
    notion_status_published: str = Field(default="Publicado", alias="NOTION_STATUS_PUBLISHED")

    # Metricool
    metricool_user_token: str = Field(alias="METRICOOL_USER_TOKEN")
    metricool_user_id: str = Field(alias="METRICOOL_USER_ID")
    metricool_blog_id: str = Field(default="5822365", alias="METRICOOL_BLOG_ID")
    metricool_timezone: str = Field(default="America/Bogota", alias="METRICOOL_TIMEZONE")
    metricool_base_url: str = Field(
        default="https://app.metricool.com/api", alias="METRICOOL_BASE_URL"
    )

    timezone: str = Field(default="America/Bogota", alias="TIMEZONE")

    enable_schedule: bool = Field(default=False, alias="ENABLE_SCHEDULE")
    schedule_max_per_run: int = Field(default=5, alias="SCHEDULE_MAX_PER_RUN")
    confirm_max_per_run: int = Field(default=20, alias="CONFIRM_MAX_PER_RUN")
    sync_dates_max_per_run: int = Field(default=20, alias="SYNC_DATES_MAX_PER_RUN")
    publication_window_days: int = Field(default=7, alias="PUBLICATION_WINDOW_DAYS")

    ffmpeg_bin: str = Field(default="ffmpeg", alias="FFMPEG_BIN")
    ffprobe_bin: str = Field(default="ffprobe", alias="FFPROBE_BIN")
    instagram_video_max_width: int = Field(default=1920, alias="INSTAGRAM_VIDEO_MAX_WIDTH")
    media_work_dir: Path = Field(default=Path(".data/media-work"), alias="MEDIA_WORK_DIR")

    transfer_sh_enabled: bool = Field(default=False, alias="TRANSFER_SH_ENABLED")
    transfer_sh_url: str = Field(default="https://transfer.sh", alias="TRANSFER_SH_URL")
    s3_endpoint: str | None = Field(default=None, alias="S3_ENDPOINT")
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_access_key: str | None = Field(default=None, alias="S3_ACCESS_KEY")
    s3_secret_key: str | None = Field(default=None, alias="S3_SECRET_KEY")
    s3_public_base_url: str | None = Field(default=None, alias="S3_PUBLIC_BASE_URL")

    slack_webhook_url: str | None = Field(default=None, alias="SLACK_WEBHOOK_URL")
    slack_channel: str = Field(default="#ct-growth", alias="SLACK_CHANNEL")
    slack_dedupe_hours: int = Field(default=6, alias="SLACK_DEDUPE_HOURS")
    slack_dedupe_file: Path = Field(
        default=Path(".data/slack-dedupe.json"),
        alias="SLACK_DEDUPE_FILE",
    )

    dry_run: bool = Field(default=False, alias="DRY_RUN")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Optional ctw-cover-agent (sibling repo on CTW_COVER_AGENT_PATH)
    ctw_cover_agent_path: str | None = Field(default=None, alias="CTW_COVER_AGENT_PATH")
    dropbox_access_token: str | None = Field(default=None, alias="DROPBOX_ACCESS_TOKEN")
    require_cover_for_schedule: bool = Field(
        default=False,
        alias="REQUIRE_COVER_FOR_SCHEDULE",
    )

    def ensure_data_dirs(self) -> None:
        self.media_work_dir.mkdir(parents=True, exist_ok=True)
        self.slack_dedupe_file.parent.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
