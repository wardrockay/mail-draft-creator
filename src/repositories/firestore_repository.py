"""
Firestore Repository
====================

Data access layer for Firestore operations with proper
error handling, type safety, and retry logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from src.config import get_settings
from src.exceptions import (
    DraftNotFoundError,
    ErrorContext,
    FirestoreError,
    FollowupNotFoundError,
)
from src.logging_config import get_logger
from src.models import DraftStatus, EmailDraftDTO, EmailFollowupDTO

logger = get_logger(__name__)


class FirestoreRepository:
    """
    Repository for Firestore database operations.
    
    Implements the Repository pattern for clean separation
    between business logic and data access.
    
    Example:
        >>> repo = FirestoreRepository()
        >>> draft = repo.get_draft("draft_id_123")
        >>> repo.update_draft_status("draft_id_123", DraftStatus.SENT)
    """
    
    def __init__(self, client: Optional[firestore.Client] = None) -> None:
        """
        Initialize the repository.
        
        Args:
            client: Optional Firestore client. If not provided, creates one.
        """
        self._client = client or firestore.Client()
        self._settings = get_settings()
        self._drafts_collection = self._settings.firestore.drafts_collection
        self._followups_collection = self._settings.firestore.followups_collection
    
    @property
    def db(self) -> firestore.Client:
        """Get the Firestore client."""
        return self._client
    
    # ========================================================================
    # Draft Operations
    # ========================================================================
    
    def get_draft(self, draft_id: str) -> EmailDraftDTO:
        """
        Get a draft by ID.
        
        Args:
            draft_id: The Firestore document ID.
            
        Returns:
            EmailDraftDTO with draft data.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
            FirestoreError: If database operation fails.
        """
        try:
            doc_ref = self._client.collection(self._drafts_collection).document(draft_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                raise DraftNotFoundError(
                    draft_id=draft_id,
                    context=ErrorContext(operation="get_draft")
                )
            
            logger.debug("Draft retrieved", draft_id=draft_id)
            return EmailDraftDTO.from_firestore(doc.id, doc.to_dict() or {})
            
        except DraftNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to get draft", draft_id=draft_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to retrieve draft: {e}",
                context=ErrorContext(
                    operation="get_draft",
                    resource_id=draft_id,
                    resource_type="email_draft"
                ),
                cause=e
            )
    
    def get_draft_raw(self, draft_id: str) -> dict[str, Any]:
        """
        Get raw draft data as dictionary.
        
        Args:
            draft_id: The Firestore document ID.
            
        Returns:
            Dictionary with draft data.
            
        Raises:
            DraftNotFoundError: If draft doesn't exist.
        """
        try:
            doc_ref = self._client.collection(self._drafts_collection).document(draft_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                raise DraftNotFoundError(draft_id=draft_id)
            
            return doc.to_dict() or {}
            
        except DraftNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to get draft raw", draft_id=draft_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to retrieve draft: {e}",
                context=ErrorContext(operation="get_draft_raw", resource_id=draft_id),
                cause=e
            )
    
    def update_draft(self, draft_id: str, data: dict[str, Any]) -> None:
        """
        Update a draft document.
        
        Args:
            draft_id: The Firestore document ID.
            data: Dictionary of fields to update.
            
        Raises:
            FirestoreError: If update fails.
        """
        try:
            doc_ref = self._client.collection(self._drafts_collection).document(draft_id)
            doc_ref.update(data)
            logger.info("Draft updated", draft_id=draft_id, fields=list(data.keys()))
            
        except Exception as e:
            logger.error("Failed to update draft", draft_id=draft_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to update draft: {e}",
                context=ErrorContext(operation="update_draft", resource_id=draft_id),
                cause=e
            )
    
    def update_draft_status(
        self,
        draft_id: str,
        status: DraftStatus,
        additional_data: Optional[dict[str, Any]] = None
    ) -> None:
        """
        Update draft status with optional additional data.
        
        Args:
            draft_id: The Firestore document ID.
            status: New status value.
            additional_data: Optional additional fields to update.
        """
        data = {"status": status.value}
        if additional_data:
            data.update(additional_data)
        self.update_draft(draft_id, data)
    
    def mark_draft_sent(
        self,
        draft_id: str,
        message_id: str,
        thread_id: str,
        sent_at: Optional[datetime] = None
    ) -> None:
        """
        Mark a draft as sent with Gmail metadata.
        
        Args:
            draft_id: The Firestore document ID.
            message_id: Gmail message ID.
            thread_id: Gmail thread ID.
            sent_at: When the email was sent.
        """
        self.update_draft(draft_id, {
            "status": DraftStatus.SENT.value,
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "message_id": message_id,  # Keep for backward compatibility
            "thread_id": thread_id,    # Keep for backward compatibility
            "sent_at": sent_at or datetime.utcnow(),
        })
    
    def create_draft(self, data: dict[str, Any]) -> str:
        """
        Create a new draft document.
        
        Args:
            data: Draft data.
            
        Returns:
            The new document ID.
        """
        try:
            # Add created_at if not present
            if "created_at" not in data:
                data["created_at"] = datetime.utcnow()
            
            doc_ref = self._client.collection(self._drafts_collection).document()
            doc_ref.set(data)
            
            logger.info("Draft created", draft_id=doc_ref.id)
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to create draft", error=str(e))
            raise FirestoreError(
                message=f"Failed to create draft: {e}",
                context=ErrorContext(operation="create_draft"),
                cause=e
            )
    
    def migrate_message_id_fields(self, limit: Optional[int] = None) -> dict[str, Any]:
        """
        Migrate drafts from message_id/thread_id to gmail_message_id/gmail_thread_id.
        
        Args:
            limit: Maximum number of drafts to migrate (None = all).
            
        Returns:
            Dictionary with migration results.
        """
        try:
            migrated_count = 0
            skipped_count = 0
            error_count = 0
            
            query = self._client.collection(self._drafts_collection)
            
            if limit:
                query = query.limit(limit)
            
            for doc in query.stream():
                try:
                    data = doc.to_dict()
                    
                    # Check if migration is needed
                    has_old_fields = "message_id" in data or "thread_id" in data
                    has_new_fields = "gmail_message_id" in data and "gmail_thread_id" in data
                    
                    if not has_old_fields or has_new_fields:
                        skipped_count += 1
                        continue
                    
                    # Migrate fields
                    update_data = {}
                    
                    if "message_id" in data and data["message_id"]:
                        update_data["gmail_message_id"] = data["message_id"]
                    
                    if "thread_id" in data and data["thread_id"]:
                        update_data["gmail_thread_id"] = data["thread_id"]
                    
                    if update_data:
                        doc.reference.update(update_data)
                        migrated_count += 1
                        logger.info(
                            "Migrated draft fields",
                            draft_id=doc.id,
                            fields=list(update_data.keys())
                        )
                    else:
                        skipped_count += 1
                        
                except Exception as e:
                    error_count += 1
                    logger.error(
                        "Failed to migrate draft",
                        draft_id=doc.id,
                        error=str(e)
                    )
            
            logger.info(
                "Migration complete",
                migrated=migrated_count,
                skipped=skipped_count,
                errors=error_count
            )
            
            return {
                "migrated_count": migrated_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "total_processed": migrated_count + skipped_count + error_count
            }
            
        except Exception as e:
            logger.error("Migration failed", error=str(e))
            raise FirestoreError(
                message=f"Migration failed: {e}",
                context=ErrorContext(operation="migrate_message_id_fields"),
                cause=e
            )
    
    def get_drafts_by_status(
        self,
        status: DraftStatus,
        limit: int = 100
    ) -> list[EmailDraftDTO]:
        """
        Get drafts by status.
        
        Args:
            status: Status to filter by.
            limit: Maximum number of results.
            
        Returns:
            List of EmailDraftDTO objects.
        """
        try:
            query = (
                self._client.collection(self._drafts_collection)
                .where(filter=FieldFilter("status", "==", status.value))
                .limit(limit)
            )
            
            drafts = []
            for doc in query.stream():
                drafts.append(EmailDraftDTO.from_firestore(doc.id, doc.to_dict() or {}))
            
            logger.debug("Drafts retrieved by status", status=status.value, count=len(drafts))
            return drafts
            
        except Exception as e:
            logger.error("Failed to get drafts by status", status=status.value, error=str(e))
            raise FirestoreError(
                message=f"Failed to query drafts: {e}",
                context=ErrorContext(operation="get_drafts_by_status"),
                cause=e
            )
    
    # ========================================================================
    # Followup Operations
    # ========================================================================
    
    def get_followup(self, followup_id: str) -> EmailFollowupDTO:
        """
        Get a followup by ID.
        
        Args:
            followup_id: The Firestore document ID.
            
        Returns:
            EmailFollowupDTO with followup data.
            
        Raises:
            FollowupNotFoundError: If followup doesn't exist.
        """
        try:
            doc_ref = self._client.collection(self._followups_collection).document(followup_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                raise FollowupNotFoundError(followup_id=followup_id)
            
            logger.debug("Followup retrieved", followup_id=followup_id)
            return EmailFollowupDTO.from_firestore(doc.id, doc.to_dict() or {})
            
        except FollowupNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to get followup", followup_id=followup_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to retrieve followup: {e}",
                context=ErrorContext(operation="get_followup", resource_id=followup_id),
                cause=e
            )
    
    def get_followup_raw(self, followup_id: str) -> dict[str, Any]:
        """
        Get raw followup data as dictionary.
        
        Args:
            followup_id: The Firestore document ID.
            
        Returns:
            Dictionary with followup data.
        """
        try:
            doc_ref = self._client.collection(self._followups_collection).document(followup_id)
            doc = doc_ref.get()
            
            if not doc.exists:
                raise FollowupNotFoundError(followup_id=followup_id)
            
            return doc.to_dict() or {}
            
        except FollowupNotFoundError:
            raise
        except Exception as e:
            logger.error("Failed to get followup raw", followup_id=followup_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to retrieve followup: {e}",
                context=ErrorContext(operation="get_followup_raw", resource_id=followup_id),
                cause=e
            )
    
    def update_followup(self, followup_id: str, data: dict[str, Any]) -> None:
        """
        Update a followup document.
        
        Args:
            followup_id: The Firestore document ID.
            data: Dictionary of fields to update.
        """
        try:
            doc_ref = self._client.collection(self._followups_collection).document(followup_id)
            doc_ref.update(data)
            logger.info("Followup updated", followup_id=followup_id, fields=list(data.keys()))
            
        except Exception as e:
            logger.error("Failed to update followup", followup_id=followup_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to update followup: {e}",
                context=ErrorContext(operation="update_followup", resource_id=followup_id),
                cause=e
            )
    
    def mark_followup_sent(
        self,
        followup_id: str,
        message_id: str,
        thread_id: str,
        sent_at: Optional[datetime] = None
    ) -> None:
        """
        Mark a followup as sent with Gmail metadata.
        
        Args:
            followup_id: The Firestore document ID.
            message_id: Gmail message ID.
            thread_id: Gmail thread ID.
            sent_at: When the email was sent.
        """
        self.update_followup(followup_id, {
            "status": DraftStatus.SENT.value,
            "message_id": message_id,
            "thread_id": thread_id,
            "sent_at": sent_at or datetime.utcnow(),
        })
    
    def create_followup(self, data: dict[str, Any]) -> str:
        """
        Create a new followup document.
        
        Args:
            data: Followup data.
            
        Returns:
            The new document ID.
        """
        try:
            if "created_at" not in data:
                data["created_at"] = datetime.utcnow()
            
            doc_ref = self._client.collection(self._followups_collection).document()
            doc_ref.set(data)
            
            logger.info("Followup created", followup_id=doc_ref.id)
            return doc_ref.id
            
        except Exception as e:
            logger.error("Failed to create followup", error=str(e))
            raise FirestoreError(
                message=f"Failed to create followup: {e}",
                context=ErrorContext(operation="create_followup"),
                cause=e
            )
    
    def get_followups_for_draft(
        self,
        draft_id: str,
        status: Optional[DraftStatus] = None
    ) -> list[EmailFollowupDTO]:
        """
        Get all followups for a specific draft.
        
        Args:
            draft_id: The original draft ID.
            status: Optional status filter.
            
        Returns:
            List of followups ordered by followup_number.
        """
        try:
            query = self._client.collection(self._followups_collection).where(
                filter=FieldFilter("original_draft_id", "==", draft_id)
            )
            
            if status:
                query = query.where(filter=FieldFilter("status", "==", status.value))
            
            followups = []
            for doc in query.stream():
                followups.append(EmailFollowupDTO.from_firestore(doc.id, doc.to_dict() or {}))
            
            # Sort by followup_number
            followups.sort(key=lambda f: f.followup_number)
            
            logger.debug(
                "Followups retrieved for draft",
                draft_id=draft_id,
                count=len(followups)
            )
            return followups
            
        except Exception as e:
            logger.error("Failed to get followups", draft_id=draft_id, error=str(e))
            raise FirestoreError(
                message=f"Failed to query followups: {e}",
                context=ErrorContext(operation="get_followups_for_draft"),
                cause=e
            )


# Singleton instance for convenience
_repository: Optional[FirestoreRepository] = None


def get_repository() -> FirestoreRepository:
    """
    Get the global FirestoreRepository instance.
    
    Returns:
        FirestoreRepository singleton instance.
    """
    global _repository
    if _repository is None:
        _repository = FirestoreRepository()
    return _repository
