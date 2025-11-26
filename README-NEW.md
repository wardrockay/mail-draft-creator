# Draft Creator Service

Professional-grade email draft creation and sending service with Gmail API integration and tracking capabilities.

## Architecture

```
draft-creator/
├── src/
│   ├── __init__.py           # Package info
│   ├── app.py                # Flask application factory
│   ├── config.py             # Pydantic settings
│   ├── exceptions.py         # Custom exception hierarchy
│   ├── logging_config.py     # Structured JSON logging
│   ├── models.py             # Pydantic request/response models
│   ├── repositories/
│   │   └── firestore_repository.py  # Data access layer
│   └── services/
│       ├── draft_service.py  # Business logic
│       └── gmail_service.py  # Gmail API operations
├── tests/
│   ├── conftest.py           # Test fixtures
│   ├── test_draft_service.py
│   └── test_models.py
├── main.py                   # Legacy entry point (deprecated)
├── pyproject.toml            # Tool configuration
├── requirements.txt          # Production dependencies
└── requirements-dev.txt      # Development dependencies
```

## Features

- **Domain-Wide Delegation**: Sends emails on behalf of users via Google Workspace
- **Markdown Support**: Converts Markdown to HTML for rich email formatting
- **Tracking Pixels**: Automatic open tracking integration
- **Test Mode**: Send test emails without affecting real prospects
- **Followup Threading**: Maintains email threads for followup sequences
- **Structured Logging**: Cloud Run optimized JSON logging

## Configuration

The service uses Pydantic Settings for configuration management:

```python
from src.config import get_settings

settings = get_settings()
print(settings.gmail.delegated_user)
print(settings.tracking.pixel_url)
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GMAIL_DELEGATED_USER` | Email for domain-wide delegation | Required |
| `GCP_PROJECT_ID` | Google Cloud project ID | `light-and-shutter` |
| `SERVICE_ACCOUNT_EMAIL` | Service account email | See config |
| `TRACKING_BASE_URL` | Base URL for tracking pixels | Cloud Run URL |
| `ENVIRONMENT` | `development`, `staging`, `production` | `development` |
| `DEBUG` | Enable debug mode | `false` |

## API Endpoints

### POST /send-draft

Send an email draft.

```json
{
  "draft_id": "firestore_document_id",
  "test_mode": false,
  "test_email": "test@example.com"
}
```

### POST /send-followup

Send a followup email in the same thread.

```json
{
  "followup_id": "firestore_document_id",
  "test_mode": false,
  "test_email": "test@example.com"
}
```

### POST /resend-to-another

Resend a draft to a different address (for forwarding to colleagues).

```json
{
  "draft_id": "original_draft_id",
  "new_recipient_email": "colleague@company.com",
  "new_recipient_name": "Colleague Name"
}
```

### GET /health

Health check endpoint.

## Development

### Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements-dev.txt

# Copy environment file
cp .env.example .env
```

### Running Tests

```bash
# Run all tests
pytest

# With coverage
pytest --cov=src --cov-report=html

# Type checking
mypy src/

# Linting
ruff check src/
```

### Running Locally

```bash
# New modular app
python -m src.app

# Or with Gunicorn
gunicorn "src.app:app" --bind 0.0.0.0:8080
```

## Error Handling

The service uses a structured exception hierarchy:

```python
from src.exceptions import (
    DraftCreatorError,     # Base exception
    GmailError,            # Gmail API errors
    GmailAuthError,        # Authentication failures
    GmailSendError,        # Sending failures
    DraftNotFoundError,    # Draft doesn't exist
    ValidationError,       # Request validation
)
```

Each exception includes:
- Error code for programmatic handling
- Human-readable message
- Context for debugging
- Original exception preservation

## Logging

Uses structured JSON logging optimized for Google Cloud Logging:

```python
from src.logging_config import get_logger

logger = get_logger(__name__)
logger.info("Processing draft", draft_id="abc123", status="pending")
logger.error("Failed to send", error=str(e), recipient="test@example.com")
```

## Type Safety

All code is fully typed and validated with:
- Pydantic models for request/response validation
- Type hints throughout
- MyPy for static type checking

## Deployment

### Cloud Run

```bash
# Build
gcloud builds submit --tag gcr.io/PROJECT_ID/draft-creator

# Deploy
gcloud run deploy draft-creator \
  --image gcr.io/PROJECT_ID/draft-creator \
  --region europe-west1 \
  --platform managed \
  --allow-unauthenticated
```

## License

Proprietary - LightAndShutter
