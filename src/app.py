"""
Flask Application
=================

Main Flask application with proper error handling,
request validation, and structured logging.
"""

from __future__ import annotations

import os
from typing import Any

from flask import Flask, Response, jsonify, request
from pydantic import ValidationError as PydanticValidationError

from src.config import get_settings
from src.exceptions import (
    DraftCreatorError,
    DraftNotFoundError,
    FollowupNotFoundError,
    GmailError,
    ValidationError,
)
from src.logging_config import get_logger, set_request_id
from src.models import (
    ResendToAnotherRequest,
    SendDraftRequest,
)
from src.services import get_draft_service

# Initialize logger
logger = get_logger(__name__)


def create_app() -> Flask:
    """
    Application factory for Flask app.
    
    Returns:
        Configured Flask application.
    """
    app = Flask(__name__)
    settings = get_settings()
    
    # Configuration
    app.config["DEBUG"] = settings.debug
    app.config["JSON_SORT_KEYS"] = False
    
    # Register error handlers
    register_error_handlers(app)
    
    # Register middleware
    register_middleware(app)
    
    # Register routes
    register_routes(app)
    
    logger.info(
        "Application initialized",
        environment=settings.environment.value,
        debug=settings.debug
    )
    
    return app


def register_error_handlers(app: Flask) -> None:
    """Register error handlers for the application."""
    
    @app.errorhandler(DraftCreatorError)
    def handle_draft_creator_error(error: DraftCreatorError) -> tuple[Response, int]:
        """Handle custom application errors."""
        logger.error(
            f"Application error: {error.message}",
            error_code=error.code.value,
            context=error.context.to_dict() if error.context else None
        )
        
        status_code = 500
        if isinstance(error, (DraftNotFoundError, FollowupNotFoundError)):
            status_code = 404
        elif isinstance(error, ValidationError):
            status_code = 400
        elif isinstance(error, GmailError):
            status_code = 502
        
        return jsonify(error.to_dict()), status_code
    
    @app.errorhandler(PydanticValidationError)
    def handle_pydantic_validation_error(error: PydanticValidationError) -> tuple[Response, int]:
        """Handle Pydantic validation errors."""
        logger.warning("Validation error", errors=error.errors())
        return jsonify({
            "error": True,
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": error.errors()
        }), 400
    
    @app.errorhandler(400)
    def handle_bad_request(error: Any) -> tuple[Response, int]:
        """Handle bad request errors."""
        return jsonify({
            "error": True,
            "code": "BAD_REQUEST",
            "message": str(error.description) if hasattr(error, "description") else "Bad request"
        }), 400
    
    @app.errorhandler(404)
    def handle_not_found(error: Any) -> tuple[Response, int]:
        """Handle not found errors."""
        return jsonify({
            "error": True,
            "code": "NOT_FOUND",
            "message": "Resource not found"
        }), 404
    
    @app.errorhandler(500)
    def handle_internal_error(error: Any) -> tuple[Response, int]:
        """Handle internal server errors."""
        logger.error("Internal server error", error=str(error), exc_info=True)
        return jsonify({
            "error": True,
            "code": "INTERNAL_ERROR",
            "message": "An internal error occurred"
        }), 500


def register_middleware(app: Flask) -> None:
    """Register middleware for the application."""
    
    @app.before_request
    def before_request() -> None:
        """Set up request context."""
        # Get or generate request ID
        request_id = request.headers.get("X-Request-ID") or request.headers.get("X-Cloud-Trace-Context")
        set_request_id(request_id)
        
        logger.debug(
            "Request started",
            method=request.method,
            path=request.path,
            content_type=request.content_type
        )
    
    @app.after_request
    def after_request(response: Response) -> Response:
        """Log request completion."""
        logger.debug(
            "Request completed",
            method=request.method,
            path=request.path,
            status_code=response.status_code
        )
        return response


def register_routes(app: Flask) -> None:
    """Register application routes."""
    
    @app.route("/health", methods=["GET"])
    def health_check() -> tuple[Response, int]:
        """
        Health check endpoint.
        
        Returns:
            JSON response with health status.
        """
        return jsonify({
            "status": "healthy",
            "service": "draft-creator",
            "version": "2.0.0"
        }), 200
    
    @app.route("/draft/<draft_id>", methods=["GET"])
    def get_draft(draft_id: str) -> tuple[Response, int]:
        """
        Get a draft by ID.
        
        Args:
            draft_id: Firestore document ID
        
        Returns:
            JSON response with draft data.
        """
        from google.cloud import firestore
        
        settings = get_settings()
        db = firestore.Client()
        
        draft_ref = db.collection(settings.firestore.drafts_collection).document(draft_id)
        draft_doc = draft_ref.get()
        
        if not draft_doc.exists:
            raise DraftNotFoundError(draft_id=draft_id)
        
        draft_data = draft_doc.to_dict()
        draft_data["id"] = draft_doc.id
        
        return jsonify(draft_data), 200
    
    @app.route("/drafts/fields", methods=["GET"])
    def get_all_draft_fields() -> tuple[Response, int]:
        """
        Get all unique fields found across all drafts.
        
        Returns:
            JSON response with list of unique field names.
        """
        from google.cloud import firestore
        
        settings = get_settings()
        db = firestore.Client()
        
        unique_fields = set()
        
        # Parcourir tous les drafts
        drafts_ref = db.collection(settings.firestore.drafts_collection)
        for doc in drafts_ref.stream():
            draft_data = doc.to_dict()
            if draft_data:
                unique_fields.update(draft_data.keys())
        
        return jsonify({
            "fields": sorted(list(unique_fields)),
            "count": len(unique_fields)
        }), 200
    
    @app.route("/", methods=["POST"])
    def create_draft() -> tuple[Response, int]:
        """
        Create a new draft in Firestore.
        
        This is the main endpoint called by mail-writer to save generated emails.
        
        Request body:
            - to: Recipient email
            - subject: Email subject
            - message: Email body (markdown)
            - x_external_id: External ID (Pharow)
            - version_group_id: (optional) Group ID for draft versions
            - odoo_id: (optional) Odoo lead ID
            - contact_name, partner_name, function, website, description: Contact info
            - status: (optional) Draft status, default "pending"
            - error_message: (optional) Error message if status="error"
            - reply_to_thread_id, reply_to_message_id, original_subject: Thread info for followups
            - followup_number: (optional) Followup number (1-4)
        
        Returns:
            JSON response with draft_id and version_group_id.
        """
        import uuid
        from datetime import datetime, timezone
        from google.cloud import firestore
        
        data = request.get_json(force=True)
        
        # Extract data
        to = data.get("to", "")
        subject = data.get("subject", "")
        message = data.get("message", "")
        x_external_id = data.get("x_external_id", "")
        version_group_id = data.get("version_group_id", "") or str(uuid.uuid4())
        odoo_id = data.get("odoo_id")
        status = data.get("status", "pending")
        error_message = data.get("error_message")
        
        # Thread info for followups
        reply_to_thread_id = data.get("reply_to_thread_id", "")
        reply_to_message_id = data.get("reply_to_message_id", "")
        original_subject = data.get("original_subject", "")
        followup_number = data.get("followup_number", 0)
        
        # Contact info
        contact_info = {
            "contact_name": data.get("contact_name", ""),
            "partner_name": data.get("partner_name", ""),
            "function": data.get("function", ""),
            "website": data.get("website", ""),
            "description": data.get("description", "")
        }
        
        # Build draft data
        draft_id = str(uuid.uuid4())
        draft_data = {
            "to": to,
            "subject": subject,
            "body": message,
            "created_at": datetime.now(timezone.utc),
            "status": status,
            "version_group_id": version_group_id,
        }
        
        if x_external_id:
            draft_data["x_external_id"] = x_external_id
        if odoo_id is not None:
            draft_data["odoo_id"] = odoo_id
        if error_message:
            draft_data["error_message"] = error_message
        if reply_to_thread_id:
            draft_data["reply_to_thread_id"] = reply_to_thread_id
        if reply_to_message_id:
            draft_data["reply_to_message_id"] = reply_to_message_id
        if original_subject:
            draft_data["original_subject"] = original_subject
        if followup_number > 0:
            draft_data["followup_number"] = followup_number
            draft_data["is_followup"] = True
        
        # Add contact info
        for key, value in contact_info.items():
            if value:
                draft_data[key] = value
        
        # Save to Firestore
        db = firestore.Client()
        settings = get_settings()
        doc_ref = db.collection(settings.firestore.drafts_collection).document(draft_id)
        doc_ref.set(draft_data)
        
        logger.info(
            "✅ Draft saved to Firestore",
            draft_id=draft_id,
            version_group_id=version_group_id,
            is_followup=followup_number > 0
        )
        
        return jsonify({
            "status": "ok",
            "mode": "draft",
            "draft_id": draft_id,
            "version_group_id": version_group_id
        }), 200
    
    @app.route("/send-draft", methods=["POST"])
    def send_draft() -> tuple[Response, int]:
        """
        Send an email draft.
        
        Request body:
            - draft_id: Firestore document ID
            - test_mode: (optional) If true, send to test_email
            - test_email: (optional) Email for test mode
        
        Returns:
            JSON response with message_id and thread_id.
        """
        data = request.get_json(force=True)
        
        # Validate request
        req = SendDraftRequest(**data)
        
        # Get service and send
        service = get_draft_service()
        result = service.send_draft(
            draft_id=req.draft_id,
            test_mode=req.test_mode,
            test_email=req.test_email
        )
        
        return jsonify(result), 200
    
    @app.route("/resend-to-another", methods=["POST"])
    def resend_to_another() -> tuple[Response, int]:
        """
        Resend a draft to a different email address.
        
        Request body:
            - draft_id: Original draft ID
            - new_recipient_email: New recipient email
            - new_recipient_name: (optional) New recipient name
        
        Returns:
            JSON response with message_id.
        """
        data = request.get_json(force=True)
        
        # Validate request
        req = ResendToAnotherRequest(**data)
        
        # Get service and resend
        service = get_draft_service()
        result = service.resend_to_another(
            draft_id=req.draft_id,
            new_recipient_email=req.new_recipient_email,
            new_recipient_name=req.new_recipient_name
        )
        
        return jsonify(result), 200
    
    @app.route("/migrate-message-ids", methods=["POST"])
    def migrate_message_ids() -> tuple[Response, int]:
        """
        Migrate drafts from message_id/thread_id to gmail_message_id/gmail_thread_id.
        
        Request body (optional):
            - limit: Maximum number of drafts to process
        
        Returns:
            JSON response with migration results.
        """
        from src.repositories.firestore_repository import FirestoreRepository
        
        data = request.get_json(force=True) if request.data else {}
        limit = data.get("limit")
        
        # Get repository and migrate
        repo = FirestoreRepository()
        result = repo.migrate_message_id_fields(limit=limit)
        
        return jsonify(result), 200
    
    @app.route("/check-all-replies", methods=["POST"])
    def check_all_replies() -> tuple[Response, int]:
        """
        Check for replies to all sent drafts with gmail_thread_id.
        
        For each draft with a gmail_thread_id, calls gmail-notifier to check for replies
        and updates Firestore if necessary.
        
        Request body (optional):
            - limit: Maximum number of drafts to process
        
        Returns:
            JSON response with check results.
        """
        import requests
        from google.cloud import firestore
        from google.auth.transport.requests import Request as GoogleRequest
        import google.auth
        
        settings = get_settings()
        db = firestore.Client()
        
        data = request.get_json(force=True) if request.data else {}
        limit = data.get("limit", 100)
        
        # Get all sent drafts with gmail_thread_id
        drafts_ref = db.collection(settings.firestore.drafts_collection).where(
            "status", "==", "sent"
        ).where(
            "gmail_thread_id", "!=", ""
        ).limit(limit)
        
        drafts = list(drafts_ref.stream())
        
        if not drafts:
            return jsonify({
                "status": "ok",
                "message": "No drafts to check",
                "total_checked": 0,
                "replies_found": 0
            }), 200
        
        # Get ID token for gmail-notifier
        gmail_notifier_url = settings.services.gmail_notifier_url
        if not gmail_notifier_url:
            return jsonify({
                "error": True,
                "message": "GMAIL_NOTIFIER_URL not configured"
            }), 500
        
        try:
            credentials, project_id = google.auth.default()
            credentials.refresh(GoogleRequest())
            
            if hasattr(credentials, 'id_token'):
                id_token = credentials.id_token
            else:
                # Generate ID token
                sa_email = credentials.service_account_email if hasattr(credentials, 'service_account_email') else None
                if not sa_email:
                    sa_email = settings.service_account_email
                
                url = f"https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:generateIdToken"
                headers = {
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "audience": gmail_notifier_url,
                    "includeEmail": True
                }
                
                response = requests.post(url, json=payload, headers=headers, timeout=10)
                response.raise_for_status()
                id_token = response.json()["token"]
        except Exception as e:
            logger.warning(f"Could not get ID token: {e}, proceeding without auth")
            id_token = None
        
        # Check each draft for replies
        total_checked = 0
        replies_found = 0
        errors = []
        
        for doc in drafts:
            draft_id = doc.id
            draft_data = doc.to_dict()
            
            # Skip if already has a reply
            if draft_data.get("has_reply"):
                continue
            
            try:
                headers = {"Content-Type": "application/json"}
                if id_token:
                    headers["Authorization"] = f"Bearer {id_token}"
                
                response = requests.post(
                    f"{gmail_notifier_url}/fetch-reply",
                    json={"draft_id": draft_id},
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code == 200:
                    result = response.json()
                    total_checked += 1
                    
                    if result.get("has_reply"):
                        replies_found += 1
                        logger.info(f"Reply found for draft {draft_id}")
                else:
                    errors.append({
                        "draft_id": draft_id,
                        "error": f"HTTP {response.status_code}"
                    })
                    
            except Exception as e:
                errors.append({
                    "draft_id": draft_id,
                    "error": str(e)
                })
                logger.error(f"Error checking draft {draft_id}: {e}")
        
        return jsonify({
            "status": "ok",
            "total_checked": total_checked,
            "replies_found": replies_found,
            "errors": errors if errors else None
        }), 200
    
    @app.route("/delete-draft/<draft_id>", methods=["DELETE"])
    def delete_draft(draft_id: str) -> tuple[Response, int]:
        """
        Delete a draft and all related data (followups, tracking pixels, email opens).
        
        Path parameter:
            - draft_id: The draft ID to delete
        
        Returns:
            JSON response with deletion results.
        """
        from google.cloud import firestore
        
        settings = get_settings()
        db = firestore.Client()
        
        deleted = {
            "draft": False,
            "followups": 0,
            "pixel_doc": False,
            "opens": 0
        }
        
        try:
            # 1. Get draft data first (to get pixel_id)
            draft_ref = db.collection(settings.firestore.drafts_collection).document(draft_id)
            draft_doc = draft_ref.get()
            
            if not draft_doc.exists:
                return jsonify({
                    "error": True,
                    "message": f"Draft {draft_id} not found"
                }), 404
            
            draft_data = draft_doc.to_dict()
            pixel_id = draft_data.get("pixel_id")
            
            # 2. Delete all followups for this draft
            followups_ref = db.collection(settings.firestore.followups_collection).where(
                "draft_id", "==", draft_id
            )
            
            for followup_doc in followups_ref.stream():
                followup_doc.reference.delete()
                deleted["followups"] += 1
            
            # 3. Delete tracking pixel document and opens subcollection if exists
            if pixel_id:
                pixel_ref = db.collection(settings.firestore.pixel_opens_collection).document(pixel_id)
                pixel_doc = pixel_ref.get()
                
                if pixel_doc.exists:
                    # Delete opens subcollection
                    opens_ref = pixel_ref.collection("opens")
                    for open_doc in opens_ref.stream():
                        open_doc.reference.delete()
                        deleted["opens"] += 1
                    
                    # Delete pixel document
                    pixel_ref.delete()
                    deleted["pixel_doc"] = True
            
            # 4. Delete the draft itself
            draft_ref.delete()
            deleted["draft"] = True
            
            logger.info(
                "Draft and related data deleted",
                draft_id=draft_id,
                deleted=deleted
            )
            
            return jsonify({
                "status": "ok",
                "message": f"Draft {draft_id} and related data deleted successfully",
                "deleted": deleted
            }), 200
            
        except Exception as e:
            logger.error(f"Error deleting draft {draft_id}: {e}", exc_info=True)
            return jsonify({
                "error": True,
                "message": f"Failed to delete draft: {str(e)}",
                "deleted": deleted
            }), 500
    
    @app.route("/debug/check-followup-thread-fields", methods=["GET"])
    def debug_check_followup_thread_fields() -> tuple[Response, int]:
        """
        Vérifier si tous les drafts avec followup_number > 0 ont les champs thread nécessaires.
        
        Vérifie la présence de:
        - reply_to_message_id
        - reply_to_thread_id
        
        Returns:
            JSON response avec statistiques et liste des drafts manquants.
        """
        from google.cloud import firestore
        
        settings = get_settings()
        db = firestore.Client()
        
        # Récupérer tous les drafts de relance
        drafts_ref = db.collection(settings.firestore.drafts_collection).where(
            "followup_number", ">", 0
        )
        
        total_followups = 0
        with_both_fields = 0
        missing_reply_to_message = 0
        missing_reply_to_thread = 0
        missing_both = 0
        
        drafts_with_issues = []
        
        for doc in drafts_ref.stream():
            total_followups += 1
            draft_data = doc.to_dict()
            
            has_message_id = bool(draft_data.get("reply_to_message_id"))
            has_thread_id = bool(draft_data.get("reply_to_thread_id"))
            
            if has_message_id and has_thread_id:
                with_both_fields += 1
            else:
                issue = {
                    "draft_id": doc.id,
                    "followup_number": draft_data.get("followup_number"),
                    "to": draft_data.get("to"),
                    "status": draft_data.get("status"),
                    "x_external_id": draft_data.get("x_external_id"),
                    "has_reply_to_message_id": has_message_id,
                    "has_reply_to_thread_id": has_thread_id,
                }
                
                if not has_message_id and not has_thread_id:
                    missing_both += 1
                    issue["missing"] = "both"
                elif not has_message_id:
                    missing_reply_to_message += 1
                    issue["missing"] = "reply_to_message_id"
                else:
                    missing_reply_to_thread += 1
                    issue["missing"] = "reply_to_thread_id"
                
                drafts_with_issues.append(issue)
        
        logger.info(
            f"Vérification thread fields: {total_followups} followups, {with_both_fields} avec les 2 champs"
        )
        
        return jsonify({
            "total_followup_drafts": total_followups,
            "with_both_fields": with_both_fields,
            "missing_reply_to_message_id": missing_reply_to_message,
            "missing_reply_to_thread_id": missing_reply_to_thread,
            "missing_both_fields": missing_both,
            "percentage_complete": round((with_both_fields / total_followups * 100), 2) if total_followups > 0 else 0,
            "drafts_with_issues": drafts_with_issues[:50],  # Limiter à 50 pour lisibilité
            "total_issues": len(drafts_with_issues)
        }), 200


# Create application instance
app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))
    
    logger.info(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=settings.debug)
