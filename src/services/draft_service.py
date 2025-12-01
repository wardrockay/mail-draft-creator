"""
Email Draft Service
===================

Business logic layer for email draft operations.
Orchestrates Gmail and Firestore operations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

import markdown
import requests
from google.cloud import firestore

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
    
    def _add_tracking_pixel(
        self, 
        html: str, 
        draft_id: str, 
        recipient_email: str,
        subject: str,
        is_followup: bool = False
    ) -> tuple[str, str]:
        """
        Add tracking pixel to HTML content and create Firestore tracking document.
        
        Args:
            html: HTML content.
            draft_id: Draft or followup ID for tracking.
            recipient_email: Email recipient.
            subject: Email subject.
            is_followup: Whether this is a followup email.
            
        Returns:
            Tuple of (HTML with tracking pixel, pixel_id).
        """
        # Generate unique pixel ID
        pixel_id = str(uuid.uuid4())
        
        # Create Firestore document for tracking
        db = firestore.Client()
        pixel_collection = self._settings.firestore.pixel_opens_collection
        doc_ref = db.collection(pixel_collection).document(pixel_id)
        doc_ref.set({
            "to": recipient_email,
            "subject": subject,
            "draft_id": draft_id,
            "open_count": 0,
            "created_at": datetime.utcnow(),
            "is_followup": is_followup
        })
        
        # Add pixel to HTML
        pixel_url = f"{self._settings.tracking.pixel_url}?id={pixel_id}"
        pixel_tag = f'<img src="{pixel_url}" width="1" height="1" style="display:none" alt="">'
        
        return html + pixel_tag, pixel_id
    
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
        
        logger.info(
            "Draft data retrieved",
            draft_id=draft_id,
            to_field=draft_data.get("to"),
            contact_name=draft_data.get("contact_name"),
            subject=draft_data.get("subject", "")[:50]
        )
        
        # Determine recipient
        if test_mode:
            recipient_email = test_email
            recipient_name = "Test Recipient"
            logger.info("Test mode: sending to test email", test_email=test_email)
        else:
            recipient_email = draft_data.get("to", "").strip()
            recipient_name = draft_data.get("contact_name", "").strip()
        
        if not recipient_email:
            raise ValidationError(
                message="No recipient email found in draft",
                field="recipient_email"
            )
        
        # Validate email format
        import re
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, recipient_email):
            raise ValidationError(
                message=f"Invalid recipient email format: '{recipient_email}'",
                field="recipient_email"
            )
        
        # Get sender info - use default from settings if not provided
        settings = get_settings()
        sender_email = draft_data.get("sender_email") or draft_data.get("from_address") or settings.gmail.delegated_user
        sender_name = draft_data.get("sender_name") or draft_data.get("from_name", "")
        
        # Prepare content
        subject = draft_data.get("subject", "")
        body = draft_data.get("body") or draft_data.get("content", "")
        
        # Check if this is a followup - followups are sent as new emails (not in thread)
        is_followup = draft_data.get("is_followup", False) or draft_data.get("followup_number", 0) > 0
        
        logger.info(
            "Sending draft",
            draft_id=draft_id,
            is_followup=is_followup,
            followup_number=draft_data.get("followup_number", 0)
        )
        
        # Convert to HTML and add tracking
        html_body = self._markdown_to_html(body)
        pixel_id = None
        
        if not test_mode:
            html_body, pixel_id = self._add_tracking_pixel(
                html_body, 
                draft_id,
                recipient_email,
                subject,
                is_followup
            )
        
        # Send email - followups are sent as new separate emails (no threading)
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
            update_data = {
                "message_id": result["message_id"],
                "thread_id": result["thread_id"]
            }
            
            # Add pixel_id if tracking is enabled
            if pixel_id:
                update_data["pixel_id"] = pixel_id
            
            # Mark followups so they don't trigger new followups
            if is_followup:
                update_data["no_followup"] = True
            
            self._repository.mark_draft_sent(
                draft_id=draft_id,
                message_id=result["message_id"],
                thread_id=result["thread_id"]
            )
            # Update pixel_id and no_followup flag separately
            extra_updates = {}
            if pixel_id:
                extra_updates["pixel_id"] = pixel_id
            if is_followup:
                extra_updates["no_followup"] = True
            if extra_updates:
                self._repository.update_draft(draft_id, extra_updates)
        
        logger.info(
            "Draft sent successfully",
            draft_id=draft_id,
            message_id=result["message_id"],
            pixel_id=pixel_id,
            recipient=recipient_email,
            test_mode=test_mode
        )
        
        # Schedule followups for non-test, non-followup emails
        if not test_mode and not is_followup:
            self._schedule_followups(draft_id)
        
        response = {
            "success": True,
            "message_id": result["message_id"],
            "thread_id": result["thread_id"],
            "recipient": recipient_email,
            "draft_id": draft_id
        }
        
        if pixel_id:
            response["pixel_id"] = pixel_id
        
        return response
    
    def _schedule_followups(self, draft_id: str) -> None:
        """
        Schedule followup emails for a sent draft.
        
        Calls the auto-followup service to schedule followups.
        Fails silently to not block the main send operation.
        
        Args:
            draft_id: The draft ID to schedule followups for.
        """
        settings = get_settings()
        
        if not settings.auto_followup.enabled:
            logger.debug("Auto-followup disabled, skipping", draft_id=draft_id)
            return
        
        auto_followup_url = settings.services.auto_followup_url.rstrip("/")
        if not auto_followup_url:
            logger.warning("AUTO_FOLLOWUP_URL not configured, skipping followup scheduling")
            return
        
        try:
            logger.info("Scheduling followups", draft_id=draft_id, url=auto_followup_url)
            
            response = requests.post(
                f"{auto_followup_url}/schedule-followups",
                json={"draft_id": draft_id},
                timeout=10
            )
            
            if response.status_code == 200:
                result = response.json()
                logger.info(
                    "Followups scheduled successfully",
                    draft_id=draft_id,
                    scheduled_count=result.get("scheduled_count", 0),
                    followup_ids=result.get("followup_ids", [])
                )
            else:
                logger.warning(
                    "Failed to schedule followups",
                    draft_id=draft_id,
                    status_code=response.status_code,
                    response=response.text[:200]
                )
        except requests.exceptions.Timeout:
            logger.warning("Timeout scheduling followups", draft_id=draft_id)
        except requests.exceptions.RequestException as e:
            logger.warning("Error scheduling followups", draft_id=draft_id, error=str(e))
    
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
        
        # Get sender info - use default from settings if not provided
        settings = get_settings()
        sender_email = draft_data.get("sender_email") or draft_data.get("from_address") or settings.gmail.delegated_user
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
