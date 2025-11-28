"""
Configuration Management
========================

Centralized configuration using Pydantic Settings with validation,
environment variable loading, and type safety.
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Application environment enumeration."""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class GmailSettings(BaseSettings):
    """Gmail API configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="GMAIL_",
        extra="ignore"
    )
    
    delegated_user: str = Field(
        default="",
        alias="GMAIL_USER",
        description="Email address for domain-wide delegation"
    )
    scopes: list[str] = Field(
        default=[
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.readonly"
        ],
        description="OAuth2 scopes for Gmail API"
    )


class TrackingSettings(BaseSettings):
    """Email tracking configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="TRACKING_",
        extra="ignore"
    )
    
    base_url: str = Field(
        default="https://mail-tracker-642098175556.europe-west1.run.app",
        description="Base URL for tracking pixels"
    )
    pixel_endpoint: str = Field(
        default="/pixel.png",
        description="Endpoint for tracking pixel"
    )
    
    @property
    def pixel_url(self) -> str:
        """Get full tracking pixel URL."""
        return f"{self.base_url}{self.pixel_endpoint}"


class FirestoreSettings(BaseSettings):
    """Firestore database configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="FIRESTORE_",
        extra="ignore"
    )
    
    drafts_collection: str = Field(
        default="email_drafts",
        description="Collection for email drafts"
    )
    followups_collection: str = Field(
        default="email_followups",
        description="Collection for email followups"
    )


class ServiceURLs(BaseSettings):
    """External service URLs."""
    
    model_config = SettingsConfigDict(extra="ignore")
    
    mail_writer_url: str = Field(
        default="https://mail-writer-642098175556.europe-west1.run.app",
        alias="MAIL_WRITER_URL",
        description="Mail writer service URL"
    )
    mail_tracker_url: str = Field(
        default="https://mail-tracker-642098175556.europe-west1.run.app",
        alias="MAIL_TRACKER_URL",
        description="Mail tracker service URL"
    )


class AppSettings(BaseSettings):
    """Main application settings."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )
    
    # Core settings
    environment: Environment = Field(
        default=Environment.DEVELOPMENT,
        description="Current environment"
    )
    debug: bool = Field(
        default=False,
        description="Enable debug mode"
    )
    port: int = Field(
        default=8080,
        ge=1,
        le=65535,
        description="Server port"
    )
    
    # GCP settings
    gcp_project_id: str = Field(
        default="light-and-shutter",
        alias="GCP_PROJECT_ID",
        description="Google Cloud project ID"
    )
    gcp_region: str = Field(
        default="europe-west1",
        alias="GCP_REGION",
        description="Google Cloud region"
    )
    service_account_email: str = Field(
        default="light-shutter@light-and-shutter.iam.gserviceaccount.com",
        alias="SERVICE_ACCOUNT_EMAIL",
        description="Service account email"
    )
    
    # Nested settings
    gmail: GmailSettings = Field(default_factory=GmailSettings)
    tracking: TrackingSettings = Field(default_factory=TrackingSettings)
    firestore: FirestoreSettings = Field(default_factory=FirestoreSettings)
    services: ServiceURLs = Field(default_factory=ServiceURLs)
    
    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, v: str) -> Environment:
        """Validate and convert environment string to enum."""
        if isinstance(v, Environment):
            return v
        return Environment(v.lower())
    
    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.environment == Environment.PRODUCTION
    
    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.environment == Environment.DEVELOPMENT


@lru_cache()
def get_settings() -> AppSettings:
    """
    Get cached application settings.
    
    Uses LRU cache to ensure settings are loaded only once
    and reused across the application lifecycle.
    
    Returns:
        AppSettings: The application settings instance.
    """
    return AppSettings()


# Convenience function for quick access
settings = get_settings()
