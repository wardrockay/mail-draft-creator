"""
Tests for Models
================

Unit tests for Pydantic models and validation.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from src.models import (
    CreateEmailRequest,
    DraftStatus,
    EmailDraftDTO,
    EmailFollowupDTO,
    EmailMode,
    ResendToAnotherRequest,
    SendDraftRequest,
    SendFollowupRequest,
)


class TestSendDraftRequest:
    """Tests for SendDraftRequest model."""
    
    def test_valid_request(self) -> None:
        """Test valid request creation."""
        req = SendDraftRequest(
            draft_id="draft_123",
            test_mode=False
        )
        assert req.draft_id == "draft_123"
        assert req.test_mode is False
        assert req.test_email is None
    
    def test_test_mode_with_email(self) -> None:
        """Test test mode with email."""
        req = SendDraftRequest(
            draft_id="draft_123",
            test_mode=True,
            test_email="test@example.com"
        )
        assert req.test_mode is True
        assert req.test_email == "test@example.com"
    
    def test_test_mode_without_email_fails(self) -> None:
        """Test that test mode without email fails validation."""
        with pytest.raises(ValidationError) as exc_info:
            SendDraftRequest(
                draft_id="draft_123",
                test_mode=True
            )
        
        assert "test_email is required" in str(exc_info.value)
    
    def test_empty_draft_id_fails(self) -> None:
        """Test that empty draft_id fails."""
        with pytest.raises(ValidationError):
            SendDraftRequest(draft_id="")
    
    def test_invalid_email_fails(self) -> None:
        """Test that invalid email fails."""
        with pytest.raises(ValidationError):
            SendDraftRequest(
                draft_id="draft_123",
                test_mode=True,
                test_email="not_an_email"
            )


class TestResendToAnotherRequest:
    """Tests for ResendToAnotherRequest model."""
    
    def test_valid_request(self) -> None:
        """Test valid request creation."""
        req = ResendToAnotherRequest(
            draft_id="draft_123",
            new_recipient_email="new@example.com",
            new_recipient_name="New Person"
        )
        assert req.draft_id == "draft_123"
        assert req.new_recipient_email == "new@example.com"
        assert req.new_recipient_name == "New Person"
    
    def test_without_name(self) -> None:
        """Test request without recipient name."""
        req = ResendToAnotherRequest(
            draft_id="draft_123",
            new_recipient_email="new@example.com"
        )
        assert req.new_recipient_name == ""
    
    def test_invalid_email_fails(self) -> None:
        """Test invalid email fails."""
        with pytest.raises(ValidationError):
            ResendToAnotherRequest(
                draft_id="draft_123",
                new_recipient_email="invalid"
            )


class TestEmailDraftDTO:
    """Tests for EmailDraftDTO model."""
    
    def test_from_firestore_standard_fields(self) -> None:
        """Test creation from standard Firestore fields."""
        data = {
            "subject": "Test Subject",
            "body": "Test body",
            "recipient_email": "to@example.com",
            "recipient_name": "John",
            "sender_email": "from@example.com",
            "sender_name": "Jane",
            "status": "pending",
            "created_at": datetime.utcnow(),
            "company_name": "Acme Inc"
        }
        
        dto = EmailDraftDTO.from_firestore("doc_123", data)
        
        assert dto.id == "doc_123"
        assert dto.subject == "Test Subject"
        assert dto.body == "Test body"
        assert dto.recipient_email == "to@example.com"
        assert dto.status == DraftStatus.PENDING
    
    def test_from_firestore_legacy_fields(self) -> None:
        """Test creation from legacy field names."""
        data = {
            "subject": "Test",
            "content": "Legacy body",  # Legacy field
            "to_address": "to@example.com",  # Legacy field
            "to_name": "John",
            "from_address": "from@example.com",
            "from_name": "Jane",
            "status": "sent"
        }
        
        dto = EmailDraftDTO.from_firestore("doc_456", data)
        
        assert dto.body == "Legacy body"
        assert dto.recipient_email == "to@example.com"
        assert dto.sender_email == "from@example.com"
        assert dto.status == DraftStatus.SENT
    
    def test_status_enum_mapping(self) -> None:
        """Test all status values map correctly."""
        for status in DraftStatus:
            data = {
                "subject": "Test",
                "body": "Body",
                "recipient_email": "to@example.com",
                "sender_email": "from@example.com",
                "status": status.value
            }
            dto = EmailDraftDTO.from_firestore("doc", data)
            assert dto.status == status


class TestDraftStatus:
    """Tests for DraftStatus enum."""
    
    def test_all_statuses_exist(self) -> None:
        """Test all expected statuses exist."""
        expected = ["pending", "approved", "rejected", "sent", "bounced", "replied"]
        for status in expected:
            assert DraftStatus(status) is not None
    
    def test_status_values(self) -> None:
        """Test status enum values."""
        assert DraftStatus.PENDING.value == "pending"
        assert DraftStatus.SENT.value == "sent"
        assert DraftStatus.BOUNCED.value == "bounced"


class TestCreateEmailRequest:
    """Tests for CreateEmailRequest model."""
    
    def test_subject_cleaning(self) -> None:
        """Test that subject newlines are removed."""
        req = CreateEmailRequest(
            draft_id="draft_123",
            subject="Subject with\nnewline\r\nand more",
            body="Body content",
            recipient_email="to@example.com",
            sender_email="from@example.com"
        )
        
        assert "\n" not in req.subject
        assert "\r" not in req.subject
    
    def test_mode_default(self) -> None:
        """Test default mode is DRAFT."""
        req = CreateEmailRequest(
            draft_id="draft_123",
            subject="Test",
            body="Body",
            recipient_email="to@example.com",
            sender_email="from@example.com"
        )
        
        assert req.mode == EmailMode.DRAFT
