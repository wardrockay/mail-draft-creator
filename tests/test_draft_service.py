"""
Tests for Draft Service
=======================

Unit tests for the DraftService class.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.exceptions import DraftNotFoundError, ValidationError
from src.models import DraftStatus, EmailDraftDTO
from src.services.draft_service import DraftService


@pytest.fixture
def mock_repository() -> MagicMock:
    """Create a mock repository."""
    return MagicMock()


@pytest.fixture
def mock_gmail_service() -> MagicMock:
    """Create a mock Gmail service."""
    mock = MagicMock()
    mock.send_email.return_value = {
        "message_id": "msg_123",
        "thread_id": "thread_456",
        "label_ids": ["SENT"]
    }
    return mock


@pytest.fixture
def sample_draft_data() -> dict[str, Any]:
    """Sample draft data from Firestore."""
    return {
        "subject": "Test Subject",
        "body": "Hello **World**",
        "recipient_email": "recipient@example.com",
        "recipient_name": "John Doe",
        "sender_email": "sender@company.com",
        "sender_name": "Jane Smith",
        "status": "pending",
        "created_at": datetime.utcnow()
    }


@pytest.fixture
def draft_service(mock_repository: MagicMock) -> DraftService:
    """Create DraftService with mock repository."""
    return DraftService(repository=mock_repository)


class TestDraftService:
    """Tests for DraftService."""
    
    def test_send_draft_success(
        self,
        draft_service: DraftService,
        mock_repository: MagicMock,
        mock_gmail_service: MagicMock,
        sample_draft_data: dict[str, Any]
    ) -> None:
        """Test successful draft sending."""
        draft_id = "draft_123"
        mock_repository.get_draft_raw.return_value = sample_draft_data
        
        with patch.object(
            draft_service, "_get_gmail_service", return_value=mock_gmail_service
        ):
            result = draft_service.send_draft(draft_id)
        
        assert result["success"] is True
        assert result["message_id"] == "msg_123"
        assert result["thread_id"] == "thread_456"
        assert result["recipient"] == "recipient@example.com"
        
        mock_repository.mark_draft_sent.assert_called_once_with(
            draft_id=draft_id,
            message_id="msg_123",
            thread_id="thread_456"
        )
    
    def test_send_draft_test_mode(
        self,
        draft_service: DraftService,
        mock_repository: MagicMock,
        mock_gmail_service: MagicMock,
        sample_draft_data: dict[str, Any]
    ) -> None:
        """Test draft sending in test mode."""
        draft_id = "draft_123"
        test_email = "test@example.com"
        mock_repository.get_draft_raw.return_value = sample_draft_data
        
        with patch.object(
            draft_service, "_get_gmail_service", return_value=mock_gmail_service
        ):
            result = draft_service.send_draft(
                draft_id,
                test_mode=True,
                test_email=test_email
            )
        
        assert result["success"] is True
        assert result["recipient"] == test_email
        
        # Status should NOT be updated in test mode
        mock_repository.mark_draft_sent.assert_not_called()
        
        # Email should be sent to test address
        mock_gmail_service.send_email.assert_called_once()
        call_kwargs = mock_gmail_service.send_email.call_args.kwargs
        assert call_kwargs["to_email"] == test_email
    
    def test_send_draft_test_mode_without_email_raises_error(
        self,
        draft_service: DraftService
    ) -> None:
        """Test that test mode without test_email raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            draft_service.send_draft("draft_123", test_mode=True)
        
        assert "test_email is required" in str(exc_info.value)
    
    def test_send_draft_not_found(
        self,
        draft_service: DraftService,
        mock_repository: MagicMock
    ) -> None:
        """Test handling of non-existent draft."""
        mock_repository.get_draft_raw.side_effect = DraftNotFoundError("draft_123")
        
        with pytest.raises(DraftNotFoundError):
            draft_service.send_draft("draft_123")
    
    def test_markdown_to_html_conversion(
        self,
        draft_service: DraftService
    ) -> None:
        """Test Markdown to HTML conversion."""
        markdown_text = "Hello **bold** and *italic*"
        html = draft_service._markdown_to_html(markdown_text)
        
        assert "<strong>bold</strong>" in html
        assert "<em>italic</em>" in html
    
    def test_add_tracking_pixel(
        self,
        draft_service: DraftService
    ) -> None:
        """Test tracking pixel addition."""
        html = "<p>Hello</p>"
        result = draft_service._add_tracking_pixel(html, "draft_123")
        
        assert "pixel.png" in result
        assert "id=draft_123" in result
        assert "type=draft" in result
    
    def test_add_tracking_pixel_for_followup(
        self,
        draft_service: DraftService
    ) -> None:
        """Test tracking pixel for followup emails."""
        html = "<p>Followup</p>"
        result = draft_service._add_tracking_pixel(html, "followup_456", is_followup=True)
        
        assert "id=followup_456" in result
        assert "type=followup" in result


class TestResendToAnother:
    """Tests for resend to another functionality."""
    
    def test_resend_success(
        self,
        draft_service: DraftService,
        mock_repository: MagicMock,
        mock_gmail_service: MagicMock,
        sample_draft_data: dict[str, Any]
    ) -> None:
        """Test successful resend to another address."""
        draft_id = "draft_123"
        new_email = "colleague@company.com"
        mock_repository.get_draft_raw.return_value = sample_draft_data
        
        with patch.object(
            draft_service, "_get_gmail_service", return_value=mock_gmail_service
        ):
            result = draft_service.resend_to_another(
                draft_id=draft_id,
                new_recipient_email=new_email,
                new_recipient_name="Colleague Name"
            )
        
        assert result["success"] is True
        assert result["recipient"] == new_email
        assert result["original_draft_id"] == draft_id
        
        # Email should be sent to new address
        call_kwargs = mock_gmail_service.send_email.call_args.kwargs
        assert call_kwargs["to_email"] == new_email
        assert call_kwargs["to_name"] == "Colleague Name"


class TestFollowupSending:
    """Tests for followup email sending."""
    
    @pytest.fixture
    def sample_followup_data(self) -> dict[str, Any]:
        """Sample followup data."""
        return {
            "original_draft_id": "draft_123",
            "subject": "Re: Original Subject",
            "body": "Following up on my previous email",
            "recipient_email": "recipient@example.com",
            "recipient_name": "John Doe",
            "sender_email": "sender@company.com",
            "sender_name": "Jane Smith",
            "followup_number": 1,
            "status": "pending"
        }
    
    def test_send_followup_success(
        self,
        draft_service: DraftService,
        mock_repository: MagicMock,
        mock_gmail_service: MagicMock,
        sample_followup_data: dict[str, Any],
        sample_draft_data: dict[str, Any]
    ) -> None:
        """Test successful followup sending."""
        followup_id = "followup_456"
        
        # Set up original draft with thread info
        sample_draft_data["thread_id"] = "thread_789"
        sample_draft_data["message_id"] = "msg_original"
        
        mock_repository.get_followup_raw.return_value = sample_followup_data
        mock_repository.get_draft_raw.return_value = sample_draft_data
        mock_gmail_service.get_message_headers.return_value = {
            "message-id": "<original@example.com>"
        }
        
        with patch.object(
            draft_service, "_get_gmail_service", return_value=mock_gmail_service
        ):
            result = draft_service.send_followup(followup_id)
        
        assert result["success"] is True
        assert result["followup_id"] == followup_id
        
        # Should include thread_id
        call_kwargs = mock_gmail_service.send_email.call_args.kwargs
        assert call_kwargs["thread_id"] == "thread_789"
