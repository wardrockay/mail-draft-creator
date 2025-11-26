"""
Email Draft Service
===================

Business logic layer for email draft operations.
Orchestrates Gmail and Firestore operations.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import markdown

from src.config import get_settings
from src.exceptions import (
    DraftNotFoundError,
    ErrorContext,
    GmailSendError,
    ValidationError,
)
from src.logging_config import get_logger, log_execution_time
from src.models import DraftStatus, EmailDraftDTO
from src.repositories.firestore_repository import FirestoreRepository, get_repository
from src.services.gmail_service import GmailService, GmailServiceFactory

logger = get_logger(__name__)


class DraftService:
    """
    Service for email draft operations.
    
    Coordinates between Gmail API and Firestore to manage
    the complete lifecycle of email drafts.
    
    Example:
        >>> service = DraftService()
        >>> result = service.send_draft("draft_id", test_mode=False)
        >>> print(f"Sent with message_id: {result['message_id']}")
    """
    
    # Markdown extensions for email formatting
    MARKDOWN_EXTENSIONS = [
        "nl2br",
        "tables",
        "fenced_code",
        "sane_lists",
    ]
    
    def __init__(
        self,
        repository: Optional[FirestoreRepository] = None
    ) -> None:
        """
        Initialize draft service.
        
        Args:
            repository: Optional Firestore repository. Uses singleton if not provided.
        """
        self._repository = repository or get_repository()
        self._settings = get_settings()
    
    def _get_gmail_service(self, sender_email: str) -> GmailService:
        """
        Get Gmail service for a specific sender.
        
        Args:
            sender_email: Email address of the sender (delegated user).
            
        Returns:
            GmailService configured for the sender.
        """
        return GmailServiceFactory.get_service(sender_email)
    
    def _markdown_to_html(self, text: str) -> str:
        """
        Convert Markdown text to HTML.
        
        Args:
            text: Markdown formatted text.
            
        Returns:
            HTML string.
        """
        return markdown.markdown(text, extensions=self.MARKDOWN_EXTENSIONS)
    
    def _add_tracking_pixel(self, html: str, draft_id: str, is_followup: bool = False) -> str:
        """
        Add tracking pixel to HTML content.
        
        Args:
            html: HTML content.
            draft_id: Draft or followup ID for tracking.
            is_followup: Whether this is a followup email.
            
        Returns:
            HTML with tracking pixel appended.
        """
        doc_type = "followup" if is_followup else "draft"
        pixel_url = f"{self._settings.tracking.pixel_url}?id={draft_id}&type={doc_type}"
        pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
        return html + pixel_tag
    
    @log_execution_time()
    def send_draft(
        self,
        draft_id: str,
        test_mode: bool = False,
        test_email: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Send an email draft.
        
        Retrieves the draft from Firestore, converts Markdown to HTML,
        adds tracking pixel, sends via Gmail, and updates the draft status.
        
        Args:
            draft_id: Firestore document ID.
            test_mode: If True, sends to test_email instead of real recipient.
            test_email: Email address for test mode.
            
        Returns:
            Dictionary with message_id, thread_id, and recipient.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
            ValidationError: If test mode but no test email provided.
            GmailSendError: If sending fails.
        """
        logger.info(
            "Sending draft",
            draft_id=draft_id,
            test_mode=test_mode
        )
        
        # Validate test mode
        if test_mode and not test_email:
            raise ValidationError(
                message="test_email is required when test_mode is True",
                field="test_email"
            )
        
        # Get draft data
        draft_data = self._repository.get_draft_raw(draft_id)
        
        # Determine recipient
        if test_mode:
            recipient_email = test_email
            recipient_name = "Test Recipient"
            logger.info("Test mode: sending to test email", test_email=test_email)
        else:
            recipient_email = draft_data.get("recipient_email") or draft_data.get("to_address")
            recipient_name = draft_data.get("recipient_name") or draft_data.get("to_name", "")
        
        if not recipient_email:
            raise ValidationError(
                message="No recipient email found in draft",
                field="recipient_email"
            )
        
        # Get sender info
        sender_email = draft_data.get("sender_email") or draft_data.get("from_address")
        sender_name = draft_data.get("sender_name") or draft_data.get("from_name", "")
        
        if not sender_email:
            raise ValidationError(
                message="No sender email found in draft",
                field="sender_email"
            )
        
        # Prepare content
        subject = draft_data.get("subject", "")
        body = draft_data.get("body") or draft_data.get("content", "")
        
        # Convert to HTML and add tracking
        html_body = self._markdown_to_html(body)
        if not test_mode:
            html_body = self._add_tracking_pixel(html_body, draft_id)
        
        # Send email
        gmail_service = self._get_gmail_service(sender_email)
        result = gmail_service.send_email(
            to_email=recipient_email,
            to_name=recipient_name,
            from_name=sender_name,
            subject=subject,
            html_body=html_body
        )
        
        # Update draft status (only if not test mode)
        if not test_mode:
            self._repository.mark_draft_sent(
                draft_id=draft_id,
                message_id=result["message_id"],
                thread_id=result["thread_id"]
            )
        
        logger.info(
            "Draft sent successfully",
            draft_id=draft_id,
            message_id=result["message_id"],
            recipient=recipient_email,
            test_mode=test_mode
        )
        
        return {
            "success": True,
            "message_id": result["message_id"],
            "thread_id": result["thread_id"],
            "recipient": recipient_email,
            "draft_id": draft_id
        }
    
    @log_execution_time()
    def send_followup(
        self,
        followup_id: str,
        test_mode: bool = False,
        test_email: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Send a followup email in the same thread.
        
        Args:
            followup_id: Firestore document ID of the followup.
            test_mode: If True, sends to test_email.
            test_email: Email address for test mode.
            
        Returns:
            Dictionary with message_id, thread_id, and recipient.
        """
        logger.info(
            "Sending followup",
            followup_id=followup_id,
            test_mode=test_mode
        )
        
        if test_mode and not test_email:
            raise ValidationError(
                message="test_email is required when test_mode is True",
                field="test_email"
            )
        
        # Get followup data
        followup_data = self._repository.get_followup_raw(followup_id)
        
        # Get original draft for thread info
        original_draft_id = followup_data.get("original_draft_id")
        if original_draft_id:
            original_draft = self._repository.get_draft_raw(original_draft_id)
            thread_id = original_draft.get("thread_id")
            message_id_for_reply = original_draft.get("message_id")
        else:
            thread_id = None
            message_id_for_reply = None
        
        # Determine recipient
        if test_mode:
            recipient_email = test_email
            recipient_name = "Test Recipient"
        else:
            recipient_email = followup_data.get("recipient_email") or followup_data.get("to_address")
            recipient_name = followup_data.get("recipient_name") or followup_data.get("to_name", "")
        
        # Get sender info
        sender_email = followup_data.get("sender_email") or followup_data.get("from_address")
        sender_name = followup_data.get("sender_name") or followup_data.get("from_name", "")
        
        # Prepare content
        subject = followup_data.get("subject", "")
        body = followup_data.get("body") or followup_data.get("content", "")
        
        # Convert to HTML and add tracking
        html_body = self._markdown_to_html(body)
        if not test_mode:
            html_body = self._add_tracking_pixel(html_body, followup_id, is_followup=True)
        
        # Prepare threading headers
        references = None
        in_reply_to = None
        if message_id_for_reply and not test_mode:
            gmail_service = self._get_gmail_service(sender_email)
            headers = gmail_service.get_message_headers(message_id_for_reply)
            original_message_id_header = headers.get("message-id", "")
            if original_message_id_header:
                references = original_message_id_header
                in_reply_to = original_message_id_header
        
        # Send email
        gmail_service = self._get_gmail_service(sender_email)
        result = gmail_service.send_email(
            to_email=recipient_email,
            to_name=recipient_name,
            from_name=sender_name,
            subject=subject,
            html_body=html_body,
            thread_id=thread_id if not test_mode else None,
            references=references,
            in_reply_to=in_reply_to
        )
        
        # Update followup status
        if not test_mode:
            self._repository.mark_followup_sent(
                followup_id=followup_id,
                message_id=result["message_id"],
                thread_id=result["thread_id"]
            )
        
        logger.info(
            "Followup sent successfully",
            followup_id=followup_id,
            message_id=result["message_id"],
            thread_id=result["thread_id"],
            test_mode=test_mode
        )
        
        return {
            "success": True,
            "message_id": result["message_id"],
            "thread_id": result["thread_id"],
            "recipient": recipient_email,
            "followup_id": followup_id
        }
    
    @log_execution_time()
    def resend_to_another(
        self,
        draft_id: str,
        new_recipient_email: str,
        new_recipient_name: str = ""
    ) -> dict[str, Any]:
        """
        Resend a draft to a different email address.
        
        Creates a copy of the draft and sends to the new recipient.
        The original draft remains unchanged.
        
        Args:
            draft_id: Original draft ID.
            new_recipient_email: New recipient email.
            new_recipient_name: New recipient display name.
            
        Returns:
            Dictionary with send result.
        """
        logger.info(
            "Resending to another address",
            draft_id=draft_id,
            new_recipient=new_recipient_email
        )
        
        # Get original draft
        draft_data = self._repository.get_draft_raw(draft_id)
        
        # Get sender info
        sender_email = draft_data.get("sender_email") or draft_data.get("from_address")
        sender_name = draft_data.get("sender_name") or draft_data.get("from_name", "")
        
        # Prepare content
        subject = draft_data.get("subject", "")
        body = draft_data.get("body") or draft_data.get("content", "")
        html_body = self._markdown_to_html(body)
        
        # Send without tracking (this is a forward, not a new prospect)
        gmail_service = self._get_gmail_service(sender_email)
        result = gmail_service.send_email(
            to_email=new_recipient_email,
            to_name=new_recipient_name,
            from_name=sender_name,
            subject=subject,
            html_body=html_body
        )
        
        logger.info(
            "Email resent successfully",
            draft_id=draft_id,
            new_recipient=new_recipient_email,
            message_id=result["message_id"]
        )
        
        return {
            "success": True,
            "message_id": result["message_id"],
            "thread_id": result["thread_id"],
            "recipient": new_recipient_email,
            "original_draft_id": draft_id
        }
    
    def update_draft_status(self, draft_id: str, status: DraftStatus) -> None:
        """
        Update the status of a draft.
        
        Args:
            draft_id: Draft document ID.
            status: New status.
        """
        self._repository.update_draft_status(draft_id, status)
        logger.info("Draft status updated", draft_id=draft_id, status=status.value)
    
    def get_draft(self, draft_id: str) -> EmailDraftDTO:
        """
        Get a draft by ID.
        
        Args:
            draft_id: Draft document ID.
            
        Returns:
            EmailDraftDTO with draft data.
        """
        return self._repository.get_draft(draft_id)
    
    def get_pending_drafts(self, limit: int = 100) -> list[EmailDraftDTO]:
        """
        Get all pending drafts.
        
        Args:
            limit: Maximum number to return.
            
        Returns:
            List of pending drafts.
        """
        return self._repository.get_drafts_by_status(DraftStatus.PENDING, limit)


# Singleton instance
_draft_service: Optional[DraftService] = None


def get_draft_service() -> DraftService:
    """
    Get the global DraftService instance.
    
    Returns:
        DraftService singleton.
    """
    global _draft_service
    if _draft_service is None:
        _draft_service = DraftService()
    return _draft_service
