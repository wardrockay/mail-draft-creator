"""
Custom Exception Hierarchy
==========================

Provides a structured exception hierarchy for the draft-creator service
with proper error codes, messages, and context preservation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """Standardized error codes for the application."""
    
    # Generic errors (1xxx)
    INTERNAL_ERROR = "ERR_1000"
    VALIDATION_ERROR = "ERR_1001"
    NOT_FOUND = "ERR_1002"
    CONFLICT = "ERR_1003"
    
    # Gmail errors (2xxx)
    GMAIL_AUTH_ERROR = "ERR_2000"
    GMAIL_SEND_ERROR = "ERR_2001"
    GMAIL_QUOTA_EXCEEDED = "ERR_2002"
    GMAIL_INVALID_RECIPIENT = "ERR_2003"
    GMAIL_THREAD_NOT_FOUND = "ERR_2004"
    
    # Firestore errors (3xxx)
    FIRESTORE_CONNECTION_ERROR = "ERR_3000"
    FIRESTORE_READ_ERROR = "ERR_3001"
    FIRESTORE_WRITE_ERROR = "ERR_3002"
    DRAFT_NOT_FOUND = "ERR_3003"
    FOLLOWUP_NOT_FOUND = "ERR_3004"
    
    # Service errors (4xxx)
    MAIL_WRITER_ERROR = "ERR_4000"
    MAIL_TRACKER_ERROR = "ERR_4001"
    SERVICE_UNAVAILABLE = "ERR_4002"


@dataclass
class ErrorContext:
    """Context information for error tracking and debugging."""
    
    operation: str = ""
    resource_id: Optional[str] = None
    resource_type: Optional[str] = None
    additional_info: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert context to dictionary for logging/serialization."""
        result = {"operation": self.operation}
        if self.resource_id:
            result["resource_id"] = self.resource_id
        if self.resource_type:
            result["resource_type"] = self.resource_type
        if self.additional_info:
            result.update(self.additional_info)
        return result


class DraftCreatorError(Exception):
    """
    Base exception for all draft-creator service errors.
    
    Provides structured error information including:
    - Error code for programmatic handling
    - Human-readable message
    - Context for debugging
    - Original exception preservation
    
    Example:
        >>> raise DraftCreatorError(
        ...     message="Failed to process draft",
        ...     code=ErrorCode.INTERNAL_ERROR,
        ...     context=ErrorContext(operation="send_draft", resource_id="abc123")
        ... )
    """
    
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        context: Optional[ErrorContext] = None,
        cause: Optional[Exception] = None
    ) -> None:
        """
        Initialize the exception.
        
        Args:
            message: Human-readable error description.
            code: Standardized error code.
            context: Additional context for debugging.
            cause: Original exception that caused this error.
        """
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or ErrorContext()
        self.cause = cause
    
    def to_dict(self) -> dict[str, Any]:
        """
        Convert exception to dictionary for API responses.
        
        Returns:
            Dictionary with error details suitable for JSON serialization.
        """
        result = {
            "error": True,
            "code": self.code.value,
            "message": self.message,
        }
        if self.context.operation:
            result["context"] = self.context.to_dict()
        return result
    
    def __str__(self) -> str:
        """Format exception as string with context."""
        parts = [f"[{self.code.value}] {self.message}"]
        if self.context.operation:
            parts.append(f"(operation: {self.context.operation})")
        if self.cause:
            parts.append(f"Caused by: {self.cause}")
        return " ".join(parts)


# Gmail-specific exceptions
class GmailError(DraftCreatorError):
    """Base exception for Gmail-related errors."""
    
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.GMAIL_SEND_ERROR,
        **kwargs: Any
    ) -> None:
        super().__init__(message=message, code=code, **kwargs)


class GmailAuthError(GmailError):
    """Raised when Gmail authentication fails."""
    
    def __init__(self, message: str = "Gmail authentication failed", **kwargs: Any) -> None:
        super().__init__(message=message, code=ErrorCode.GMAIL_AUTH_ERROR, **kwargs)


class GmailSendError(GmailError):
    """Raised when email sending fails."""
    
    def __init__(
        self,
        message: str = "Failed to send email",
        recipient: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        context = kwargs.pop("context", ErrorContext())
        if recipient:
            context.additional_info["recipient"] = recipient
        super().__init__(message=message, code=ErrorCode.GMAIL_SEND_ERROR, context=context, **kwargs)


class GmailQuotaExceededError(GmailError):
    """Raised when Gmail API quota is exceeded."""
    
    def __init__(self, message: str = "Gmail API quota exceeded", **kwargs: Any) -> None:
        super().__init__(message=message, code=ErrorCode.GMAIL_QUOTA_EXCEEDED, **kwargs)


class GmailThreadNotFoundError(GmailError):
    """Raised when a Gmail thread is not found."""
    
    def __init__(
        self,
        thread_id: str,
        message: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        msg = message or f"Gmail thread not found: {thread_id}"
        context = kwargs.pop("context", ErrorContext())
        context.resource_id = thread_id
        context.resource_type = "gmail_thread"
        super().__init__(message=msg, code=ErrorCode.GMAIL_THREAD_NOT_FOUND, context=context, **kwargs)


# Firestore-specific exceptions
class FirestoreError(DraftCreatorError):
    """Base exception for Firestore-related errors."""
    
    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.FIRESTORE_READ_ERROR,
        **kwargs: Any
    ) -> None:
        super().__init__(message=message, code=code, **kwargs)


class DraftNotFoundError(FirestoreError):
    """Raised when a draft document is not found."""
    
    def __init__(
        self,
        draft_id: str,
        message: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        msg = message or f"Draft not found: {draft_id}"
        context = kwargs.pop("context", ErrorContext())
        context.resource_id = draft_id
        context.resource_type = "email_draft"
        super().__init__(message=msg, code=ErrorCode.DRAFT_NOT_FOUND, context=context, **kwargs)


class FollowupNotFoundError(FirestoreError):
    """Raised when a followup document is not found."""
    
    def __init__(
        self,
        followup_id: str,
        message: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        msg = message or f"Followup not found: {followup_id}"
        context = kwargs.pop("context", ErrorContext())
        context.resource_id = followup_id
        context.resource_type = "email_followup"
        super().__init__(message=msg, code=ErrorCode.FOLLOWUP_NOT_FOUND, context=context, **kwargs)


# Validation exceptions
class ValidationError(DraftCreatorError):
    """Raised when request validation fails."""
    
    def __init__(
        self,
        message: str,
        field: Optional[str] = None,
        **kwargs: Any
    ) -> None:
        context = kwargs.pop("context", ErrorContext())
        if field:
            context.additional_info["field"] = field
        super().__init__(
            message=message,
            code=ErrorCode.VALIDATION_ERROR,
            context=context,
            **kwargs
        )


# Service exceptions
class ServiceError(DraftCreatorError):
    """Base exception for external service errors."""
    
    def __init__(
        self,
        message: str,
        service_name: str,
        code: ErrorCode = ErrorCode.SERVICE_UNAVAILABLE,
        **kwargs: Any
    ) -> None:
        context = kwargs.pop("context", ErrorContext())
        context.additional_info["service"] = service_name
        super().__init__(message=message, code=code, context=context, **kwargs)


class MailWriterError(ServiceError):
    """Raised when the mail-writer service fails."""
    
    def __init__(self, message: str = "Mail writer service error", **kwargs: Any) -> None:
        super().__init__(
            message=message,
            service_name="mail-writer",
            code=ErrorCode.MAIL_WRITER_ERROR,
            **kwargs
        )
