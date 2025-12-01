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


# Create application instance
app = create_app()


if __name__ == "__main__":
    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))
    
    logger.info(f"Starting server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=settings.debug)
