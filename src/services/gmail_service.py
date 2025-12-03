"""
Gmail Service
=============

Service layer for Gmail API operations with domain-wide delegation,
proper error handling, and email composition.
"""

from __future__ import annotations

import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional, TypedDict

import google.auth
from google.auth.transport import requests as google_requests
from google.oauth2 import service_account
from googleapiclient import errors as gmail_errors
from googleapiclient.discovery import build, Resource

from src.config import get_settings
from src.exceptions import (
    ErrorContext,
    GmailAuthError,
    GmailSendError,
    GmailThreadNotFoundError,
)
from src.logging_config import get_logger

logger = get_logger(__name__)


class EmailResult(TypedDict):
    """Result of an email send operation."""
    message_id: str
    thread_id: str
    label_ids: list[str]


class GmailService:
    """
    Service for Gmail API operations.
    
    Handles domain-wide delegation, email composition, and sending
    with proper error handling and retry logic.
    
    Example:
        >>> service = GmailService(delegated_user="user@company.com")
        >>> result = service.send_email(
        ...     to_email="recipient@example.com",
        ...     subject="Hello",
        ...     html_body="<p>World</p>"
        ... )
    """
    
    # Token URI for service account credentials
    TOKEN_URI = "https://oauth2.googleapis.com/token"
    
    def __init__(
        self,
        delegated_user: Optional[str] = None,
        service_account_email: Optional[str] = None
    ) -> None:
        """
        Initialize Gmail service with delegation.
        
        Args:
            delegated_user: Email address to impersonate.
            service_account_email: Service account email for signing.
        """
        self._settings = get_settings()
        self._delegated_user = delegated_user or self._settings.gmail.delegated_user
        self._service_account_email = (
            service_account_email or self._settings.service_account_email
        )
        self._gmail_service: Optional[Resource] = None
    
    @property
    def gmail(self) -> Resource:
        """
        Get or create Gmail API service.
        
        Uses lazy initialization with caching.
        
        Returns:
            Gmail API service resource.
            
        Raises:
            GmailAuthError: If authentication fails.
        """
        if self._gmail_service is None:
            self._gmail_service = self._create_gmail_service()
        return self._gmail_service
    
    def _create_gmail_service(self) -> Resource:
        """
        Create authenticated Gmail service with domain-wide delegation.
        
        Uses Google's recommended approach for Cloud Run:
        1. Get default credentials from the environment (Cloud Run SA)
        2. Use the IAM Credentials API to sign a JWT
        3. Exchange the signed JWT for an access token with subject claim
        
        Returns:
            Gmail API service resource.
        """
        try:
            logger.info(
                "Creating Gmail service",
                delegated_user=self._delegated_user,
                service_account=self._service_account_email
            )
            
            import time
            import json
            import requests
            
            # Get the default credentials (Cloud Run's service account)
            source_credentials, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            
            # Refresh to get access token
            source_credentials.refresh(google_requests.Request())
            
            # Create JWT claims for domain-wide delegation
            now = int(time.time())
            claims = {
                "iss": self._service_account_email,
                "sub": self._delegated_user,  # The user to impersonate
                "scope": " ".join(self._settings.gmail.scopes),
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600
            }
            
            # Sign the JWT using IAM Credentials API
            sign_url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{self._service_account_email}:signJwt"
            
            sign_response = requests.post(
                sign_url,
                headers={
                    "Authorization": f"Bearer {source_credentials.token}",
                    "Content-Type": "application/json"
                },
                json={"payload": json.dumps(claims)}
            )
            
            if sign_response.status_code != 200:
                raise Exception(f"Failed to sign JWT: {sign_response.text}")
            
            signed_jwt = sign_response.json()["signedJwt"]
            
            # Exchange signed JWT for access token
            token_response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": signed_jwt
                }
            )
            
            if token_response.status_code != 200:
                raise Exception(f"Failed to get access token: {token_response.text}")
            
            access_token = token_response.json()["access_token"]
            
            # Create credentials with the access token
            from google.oauth2.credentials import Credentials as OAuth2Credentials
            
            credentials = OAuth2Credentials(token=access_token)
            
            # Build Gmail service
            service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False
            )
            
            logger.info("Gmail service created successfully")
            return service
            
        except Exception as e:
            logger.error(
                "Failed to create Gmail service",
                error=str(e),
                delegated_user=self._delegated_user,
                exc_info=True
            )
            raise GmailAuthError(
                message=f"Failed to authenticate with Gmail API: {e}",
                context=ErrorContext(
                    operation="create_gmail_service",
                    additional_info={"delegated_user": self._delegated_user}
                ),
                cause=e
            )
    
    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        to_name: str = "",
        from_name: str = "",
        thread_id: Optional[str] = None,
        references: Optional[str] = None,
        in_reply_to: Optional[str] = None
    ) -> EmailResult:
        """
        Send an email.
        
        Args:
            to_email: Recipient email address.
            subject: Email subject.
            html_body: HTML body content.
            to_name: Recipient display name.
            from_name: Sender display name.
            thread_id: Optional thread ID for replies.
            references: Optional References header.
            in_reply_to: Optional In-Reply-To header.
            
        Returns:
            EmailResult with message_id, thread_id, and label_ids.
            
        Raises:
            GmailSendError: If sending fails.
        """
        max_retries = 3
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                message = self._compose_email(
                    to_email=to_email,
                    to_name=to_name,
                    from_name=from_name,
                    subject=subject,
                    html_body=html_body,
                    references=references,
                    in_reply_to=in_reply_to
                )
                
                body: dict[str, Any] = {"raw": message}
                if thread_id:
                    body["threadId"] = thread_id
                
                result = (
                    self.gmail.users()
                    .messages()
                    .send(userId="me", body=body)
                    .execute()
                )
                
                logger.info(
                    "Email sent successfully",
                    message_id=result.get("id"),
                    thread_id=result.get("threadId"),
                    to_email=to_email,
                    attempt=attempt + 1
                )
                
                return EmailResult(
                    message_id=result.get("id", ""),
                    thread_id=result.get("threadId", ""),
                    label_ids=result.get("labelIds", [])
                )
                
            except (BrokenPipeError, ConnectionError, OSError) as e:
                last_exception = e
                logger.warning(
                    f"Connection error sending email (attempt {attempt + 1}/{max_retries}): {e}",
                    to_email=to_email
                )
                if attempt < max_retries - 1:
                    # Refresh service and retry
                    self.refresh_service()
                    import time
                    time.sleep(1)  # Wait longer between send retries
                    
            except gmail_errors.HttpError as e:
                logger.error(
                    "Gmail API error",
                    error=str(e),
                    to_email=to_email,
                    subject=subject
                )
                raise GmailSendError(
                    message=f"Gmail API error: {e}",
                    recipient=to_email,
                    context=ErrorContext(operation="send_email"),
                    cause=e
                )
            except Exception as e:
                last_exception = e
                logger.error("Failed to send email", error=str(e), to_email=to_email)
                # Don't retry on unknown exceptions
                break
        
        # If we get here, all retries failed
        logger.error(
            "Failed to send email after all retries",
            error=str(last_exception),
            to_email=to_email,
            attempts=max_retries
        )
        raise GmailSendError(
            message=f"Failed to send email: {last_exception}",
            recipient=to_email,
            cause=last_exception
        )
    
    def create_draft(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        to_name: str = "",
        from_name: str = "",
        thread_id: Optional[str] = None
    ) -> dict[str, str]:
        """
        Create a Gmail draft.
        
        Args:
            to_email: Recipient email address.
            subject: Email subject.
            html_body: HTML body content.
            to_name: Recipient display name.
            from_name: Sender display name.
            thread_id: Optional thread ID.
            
        Returns:
            Dictionary with draft_id and message details.
        """
        try:
            message = self._compose_email(
                to_email=to_email,
                to_name=to_name,
                from_name=from_name,
                subject=subject,
                html_body=html_body
            )
            
            body: dict[str, Any] = {"message": {"raw": message}}
            if thread_id:
                body["message"]["threadId"] = thread_id
            
            result = (
                self.gmail.users()
                .drafts()
                .create(userId="me", body=body)
                .execute()
            )
            
            logger.info(
                "Gmail draft created",
                draft_id=result.get("id"),
                to_email=to_email
            )
            
            return {
                "draft_id": result.get("id", ""),
                "message_id": result.get("message", {}).get("id", ""),
                "thread_id": result.get("message", {}).get("threadId", ""),
            }
            
        except Exception as e:
            logger.error("Failed to create draft", error=str(e), to_email=to_email)
            raise GmailSendError(
                message=f"Failed to create draft: {e}",
                recipient=to_email,
                cause=e
            )
    
    def get_thread(self, thread_id: str) -> dict[str, Any]:
        """
        Get a Gmail thread.
        
        Args:
            thread_id: The Gmail thread ID.
            
        Returns:
            Thread data including messages.
            
        Raises:
            GmailThreadNotFoundError: If thread doesn't exist.
        """
        try:
            result = (
                self.gmail.users()
                .threads()
                .get(userId="me", id=thread_id, format="full")
                .execute()
            )
            
            logger.debug("Thread retrieved", thread_id=thread_id)
            return result
            
        except gmail_errors.HttpError as e:
            if e.resp.status == 404:
                raise GmailThreadNotFoundError(thread_id=thread_id)
            raise GmailSendError(
                message=f"Failed to get thread: {e}",
                cause=e
            )
    
    def get_message_headers(self, message_id: str) -> dict[str, str]:
        """
        Get headers from a Gmail message.
        
        Args:
            message_id: The Gmail message ID.
            
        Returns:
            Dictionary of header names to values.
        """
        try:
            result = (
                self.gmail.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata")
                .execute()
            )
            
            headers = {}
            for header in result.get("payload", {}).get("headers", []):
                headers[header.get("name", "").lower()] = header.get("value", "")
            
            return headers
            
        except Exception as e:
            logger.error("Failed to get message headers", message_id=message_id, error=str(e))
            raise GmailSendError(
                message=f"Failed to get message headers: {e}",
                cause=e
            )
    
    def _compose_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        to_name: str = "",
        from_name: str = "",
        references: Optional[str] = None,
        in_reply_to: Optional[str] = None
    ) -> str:
        """
        Compose an email message in RFC 2822 format.
        
        Args:
            to_email: Recipient email.
            subject: Email subject.
            html_body: HTML body content.
            to_name: Recipient name.
            from_name: Sender name.
            references: References header for threading.
            in_reply_to: In-Reply-To header for threading.
            
        Returns:
            Base64url encoded email message.
        """
        from email.utils import formataddr
        
        # Create multipart message
        msg = MIMEMultipart("alternative")
        
        # Set headers using formataddr for proper RFC 2822 encoding
        msg["To"] = formataddr((to_name, to_email)) if to_name else to_email
        msg["From"] = formataddr((from_name, self._delegated_user)) if from_name else self._delegated_user
        msg["Subject"] = subject
        
        logger.debug(
            "Composing email",
            to_header=msg["To"],
            from_header=msg["From"],
            subject=subject[:50]
        )
        
        # Add threading headers
        if references:
            msg["References"] = references
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        
        # Create plain text version by stripping HTML
        import re
        text_body = re.sub(r"<[^>]+>", "", html_body)
        text_body = text_body.replace("&nbsp;", " ").replace("&amp;", "&")
        
        # Attach both versions
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        # Encode to base64url
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return raw
    
    def refresh_service(self) -> None:
        """Force refresh of the Gmail service connection."""
        self._gmail_service = None
        logger.info("Gmail service marked for refresh")
    
    def get_user_signature(self) -> str:
        """
        Retrieve the Gmail signature (HTML) for the delegated user.
        
        Returns:
            HTML signature string, or empty string if not found.
        """
        max_retries = 2
        for attempt in range(max_retries):
            try:
                send_as = (
                    self.gmail.users()
                    .settings()
                    .sendAs()
                    .get(userId="me", sendAsEmail=self._delegated_user)
                    .execute()
                )
                
                signature = send_as.get("signature", "")
                
                # Add alt="" to img tags that don't have one
                if signature and "<img" in signature:
                    import re
                    signature = re.sub(
                        r'<img(?![^>]*\balt\s*=)([^>]*)>',
                        r'<img alt=""\1>',
                        signature,
                        flags=re.IGNORECASE
                    )
                
                logger.debug("Signature retrieved", signature_length=len(signature))
                return signature
                
            except (BrokenPipeError, ConnectionError, OSError) as e:
                logger.warning(
                    f"Connection error retrieving signature (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    # Refresh service and retry
                    self.refresh_service()
                    import time
                    time.sleep(0.5)
                else:
                    logger.warning("Failed to retrieve signature after retries, continuing without it")
                    return ""
            except Exception as e:
                logger.warning(f"Could not retrieve signature: {e}")
                return ""
        
        return ""


class GmailServiceFactory:
    """
    Factory for creating GmailService instances.
    
    Manages a cache of services per delegated user.
    """
    
    _instances: dict[str, GmailService] = {}
    
    @classmethod
    def get_service(cls, delegated_user: str) -> GmailService:
        """
        Get or create a GmailService for a delegated user.
        
        Args:
            delegated_user: Email address to impersonate.
            
        Returns:
            GmailService instance.
        """
        if delegated_user not in cls._instances:
            cls._instances[delegated_user] = GmailService(delegated_user=delegated_user)
        return cls._instances[delegated_user]
    
    @classmethod
    def clear_cache(cls) -> None:
        """Clear all cached service instances."""
        cls._instances.clear()
        logger.info("Gmail service cache cleared")
