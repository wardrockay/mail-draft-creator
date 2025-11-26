"""Test fixtures and configuration."""

from __future__ import annotations

from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_firestore_client() -> Generator[MagicMock, None, None]:
    """Mock Firestore client."""
    with patch("google.cloud.firestore.Client") as mock:
        yield mock


@pytest.fixture
def mock_gmail_service() -> Generator[MagicMock, None, None]:
    """Mock Gmail service."""
    with patch("src.services.gmail_service.GmailServiceFactory.get_service") as mock:
        service = MagicMock()
        service.send_email.return_value = {
            "message_id": "test_msg_id",
            "thread_id": "test_thread_id",
            "label_ids": ["SENT"]
        }
        mock.return_value = service
        yield service


@pytest.fixture
def sample_draft_document() -> dict[str, Any]:
    """Sample draft document from Firestore."""
    return {
        "subject": "Test Subject",
        "body": "Test body content with **markdown**",
        "recipient_email": "recipient@example.com",
        "recipient_name": "John Doe",
        "sender_email": "sender@company.com",
        "sender_name": "Jane Smith",
        "company_name": "Acme Corp",
        "status": "pending",
    }


@pytest.fixture
def sample_followup_document() -> dict[str, Any]:
    """Sample followup document from Firestore."""
    return {
        "original_draft_id": "draft_123",
        "subject": "Re: Test Subject",
        "body": "Following up on my previous email",
        "recipient_email": "recipient@example.com",
        "recipient_name": "John Doe",
        "sender_email": "sender@company.com",
        "sender_name": "Jane Smith",
        "followup_number": 1,
        "status": "pending",
    }
