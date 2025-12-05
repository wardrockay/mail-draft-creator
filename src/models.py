"""
Data Models
===========

Pydantic models for request/response validation and type safety.
Uses strict validation and proper serialization for all data structures.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class DraftStatus(str, Enum):
    """Status of an email draft."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SENT = "sent"
    BOUNCED = "bounced"
    REPLIED = "replied"


class EmailMode(str, Enum):
    """Email creation mode."""
    DRAFT = "draft"
    SEND = "send"


class EmailType(str, Enum):
    """Type of email being sent."""
    INITIAL = "initial"
    FOLLOWUP = "followup"


# ============================================================================
# Request Models
# ============================================================================

class BaseRequestModel(BaseModel):
    """Base model for all requests with common configuration."""
    
    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="ignore"
    )


class CreateEmailRequest(BaseRequestModel):
    """Request model for creating/sending an email."""
    
    mode: EmailMode = Field(
        default=EmailMode.DRAFT,
        description="Whether to create a draft or send directly"
    )
    draft_id: str = Field(
        ...,
        min_length=1,
        description="Firestore document ID of the draft"
    )
    subject: str = Field(
        ...,
        min_length=1,
        max_length=998,  # RFC 5322 limit
        description="Email subject line"
    )
    body: str = Field(
        ...,
        min_length=1,
        description="Email body content (can be markdown)"
    )
    recipient_email: EmailStr = Field(
        ...,
        description="Recipient email address"
    )
    recipient_name: str = Field(
        default="",
        description="Recipient display name"
    )
    sender_email: EmailStr = Field(
        ...,
        description="Sender email address (delegated)"
    )
    sender_name: str = Field(
        default="",
        description="Sender display name"
    )
    
    @field_validator("subject")
    @classmethod
    def clean_subject(cls, v: str) -> str:
        """Remove any newlines from subject."""
        return v.replace("\n", " ").replace("\r", " ").strip()


class SendDraftRequest(BaseRequestModel):
    """Request model for sending an existing draft."""
    
    draft_id: str = Field(
        ...,
        min_length=1,
        description="Firestore document ID of the draft"
    )
    test_mode: bool = Field(
        default=False,
        description="If true, send to test email instead of real recipient"
    )
    test_email: Optional[EmailStr] = Field(
        default=None,
        description="Email address for test mode"
    )
    
    @field_validator("test_email")
    @classmethod
    def validate_test_email(cls, v: Optional[str], info) -> Optional[str]:
        """Ensure test_email is provided when test_mode is True."""
        if info.data.get("test_mode") and not v:
            raise ValueError("test_email is required when test_mode is True")
        return v


class SendFollowupRequest(BaseRequestModel):
    """Request model for sending a followup email."""
    
    followup_id: str = Field(
        ...,
        min_length=1,
        description="Firestore document ID of the followup"
    )
    test_mode: bool = Field(
        default=False,
        description="If true, send to test email instead of real recipient"
    )
    test_email: Optional[EmailStr] = Field(
        default=None,
        description="Email address for test mode"
    )


class ResendToAnotherRequest(BaseRequestModel):
    """Request model for resending a draft to a different address."""
    
    draft_id: str = Field(
        ...,
        min_length=1,
        description="Firestore document ID of the draft"
    )
    new_recipient_email: EmailStr = Field(
        ...,
        description="New recipient email address"
    )
    new_recipient_name: str = Field(
        default="",
        description="New recipient display name"
    )


class GenerateFollowupRequest(BaseRequestModel):
    """Request model for generating a followup email."""
    
    draft_id: str = Field(
        ...,
        min_length=1,
        description="Firestore document ID of the original draft"
    )
    followup_number: int = Field(
        ...,
        ge=1,
        le=10,
        description="Followup sequence number"
    )
    days_since_last: int = Field(
        default=3,
        ge=1,
        description="Days since last email"
    )


# ============================================================================
# Response Models
# ============================================================================

class BaseResponseModel(BaseModel):
    """Base model for all responses."""
    
    model_config = ConfigDict(
        from_attributes=True
    )


class SuccessResponse(BaseResponseModel):
    """Generic success response."""
    
    success: bool = True
    message: str = "Operation completed successfully"
    data: Optional[dict[str, Any]] = None


class ErrorResponse(BaseResponseModel):
    """Error response with details."""
    
    error: bool = True
    code: str
    message: str
    context: Optional[dict[str, Any]] = None


class EmailSentResponse(BaseResponseModel):
    """Response after sending an email."""
    
    success: bool = True
    message_id: str = Field(
        ...,
        description="Gmail message ID"
    )
    thread_id: str = Field(
        ...,
        description="Gmail thread ID"
    )
    draft_id: str = Field(
        ...,
        description="Firestore draft ID"
    )
    recipient: str = Field(
        ...,
        description="Email recipient"
    )


class DraftCreatedResponse(BaseResponseModel):
    """Response after creating a draft."""
    
    success: bool = True
    gmail_draft_id: str = Field(
        ...,
        description="Gmail draft ID"
    )
    draft_id: str = Field(
        ...,
        description="Firestore draft ID"
    )


# ============================================================================
# Data Transfer Objects
# ============================================================================

class EmailDraftDTO(BaseModel):
    """Data transfer object for email draft."""
    
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
    
    id: str
    subject: str
    body: str
    recipient_email: str
    recipient_name: str = ""
    sender_email: str
    sender_name: str = ""
    company_name: str = ""
    status: DraftStatus = DraftStatus.PENDING
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    followup_number: int = 0
    initial_draft_id: Optional[str] = None
    notes: str = ""
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> EmailDraftDTO:
        """Create DTO from Firestore document data."""
        return cls(
            id=doc_id,
            subject=data.get("subject", ""),
            body=data.get("body", data.get("content", "")),
            recipient_email=data.get("to", ""),
            recipient_name=data.get("contact_name", ""),
            sender_email=data.get("sender_email", data.get("from_address", "")),
            sender_name=data.get("sender_name", data.get("from_name", "")),
            company_name=data.get("partner_name", ""),
            status=DraftStatus(data.get("status", "pending")),
            created_at=data.get("created_at"),
            sent_at=data.get("sent_at"),
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id"),
            followup_number=data.get("followup_number", 0),
            initial_draft_id=data.get("initial_draft_id"),
            notes=data.get("notes", "")
        )


class EmailFollowupDTO(BaseModel):
    """Data transfer object for email followup."""
    
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True
    )
    
    id: str
    original_draft_id: str
    subject: str
    body: str
    recipient_email: str
    recipient_name: str = ""
    sender_email: str
    sender_name: str = ""
    followup_number: int
    status: DraftStatus = DraftStatus.PENDING
    created_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    
    @classmethod
    def from_firestore(cls, doc_id: str, data: dict[str, Any]) -> EmailFollowupDTO:
        """Create DTO from Firestore document data."""
        return cls(
            id=doc_id,
            original_draft_id=data.get("original_draft_id", ""),
            subject=data.get("subject", ""),
            body=data.get("body", data.get("content", "")),
            recipient_email=data.get("to", ""),
            recipient_name=data.get("contact_name", ""),
            sender_email=data.get("sender_email", data.get("from_address", "")),
            sender_name=data.get("sender_name", data.get("from_name", "")),
            followup_number=data.get("followup_number", 1),
            status=DraftStatus(data.get("status", "pending")),
            created_at=data.get("created_at"),
            sent_at=data.get("sent_at"),
            message_id=data.get("message_id"),
            thread_id=data.get("thread_id")
        )
