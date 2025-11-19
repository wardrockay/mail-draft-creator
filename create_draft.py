import base64
import os
import time
import json
from email.message import EmailMessage

import functions_framework
import requests

import google.auth
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# Gmail impersonation scopes
SCOPES = ["https://mail.google.com/"]

# Workspace user to impersonate
GMAIL_USER = os.environ.get("GMAIL_USER")

# Service account running on Cloud Run (same SA configured for Domain-wide Delegation)
SA_EMAIL = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL")


# --- Debug Utils -----------------------------------------------------

def debug(msg, data=None):
    print("\n────────── DEBUG ──────────")
    print(msg)
    if data is not None:
        try:
            print(json.dumps(data, indent=2))
        except Exception:
            print(str(data))
    print("────────────────────────────\n")


# --- Step 1: Call IAMCredentials API: signJwt ------------------------

def sign_jwt_with_iam(payload: dict) -> str:
    # Retrieve Cloud Run service account access token
    creds, _ = google.auth.default()
    creds.refresh(GoogleRequest())
    access_token = creds.token

    # IAMCredentials endpoint
    url = (
        f"https://iamcredentials.googleapis.com/v1/"
        f"projects/-/serviceAccounts/{SA_EMAIL}:signJwt"
    )

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    debug("IAM SIGN URL", url)
    debug("IAM SIGN HEADERS", headers)
    debug("IAM SIGN PAYLOAD", payload)

    resp = requests.post(
        url,
        json={"payload": json.dumps(payload)},
        headers=headers,
        timeout=10,
    )

    debug("IAM SIGN STATUS", resp.status_code)
    debug("IAM SIGN RAW RESPONSE", resp.text)

    resp.raise_for_status()
    signed_jwt = resp.json()["signedJwt"]

    debug("SIGNED JWT (FIRST 150 CHARS)", signed_jwt[:150])

    return signed_jwt


# --- Step 2: Exchange JWT for Google OAuth2 access token --------------

def get_gmail_service():
    debug("ENV VARS", {
        "GMAIL_USER": GMAIL_USER,
        "SERVICE_ACCOUNT": SA_EMAIL,
    })

    now = int(time.time())

    # JWT claims for domain-wide delegation
    payload = {
        "iss": SA_EMAIL,
        "sub": GMAIL_USER,
        "scope": " ".join(SCOPES),
        "aud": "https://oauth2.googleapis.com/token",
        "iat": now,
        "exp": now + 3600,
    }

    debug("JWT CLAIMS", payload)

    # 1) Sign the JWT via IAMCredentials
    signed_jwt = sign_jwt_with_iam(payload)

    # 2) Exchange JWT→OAuth2 access token
    debug("EXCHANGING SIGNED JWT FOR OAUTH TOKEN")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        },
        timeout=10,
    )

    debug("TOKEN STATUS", token_resp.status_code)
    debug("TOKEN RAW RESPONSE", token_resp.text)

    token_resp.raise_for_status()

    access_token = token_resp.json()["access_token"]

    debug("ACCESS TOKEN (FIRST 80 CHARS)", access_token[:80])

    creds = Credentials(access_token)

    debug("BUILDING GMAIL CLIENT")

    return build("gmail", "v1", credentials=creds)


# --- Step 3: Create Gmail Draft -------------------------------------

def create_draft(service, to, subject, body):
    debug("CREATE_DRAFT INPUT", {
        "to": to,
        "subject": subject,
        "body_len": len(body),
    })

    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    draft_body = {"message": {"raw": encoded}}

    debug("DRAFT RAW (FIRST 200 CHARS)", encoded[:200])

    draft = service.users().drafts().create(
        userId="me",
        body=draft_body
    ).execute()

    debug("DRAFT RESPONSE", draft)

    return draft.get("id")


# --- Cloud Run HTTP Entry --------------------------------------------

@functions_framework.http
def hello_http(request):
    debug("REQUEST RECEIVED")

    data = request.get_json(silent=True) or {}

    debug("REQUEST JSON", data)

    to = data.get("to", "client@exemple.fr")
    subject = data.get("subject", "Email automatique")
    message = data.get("message", "Message automatique.")

    debug("GETTING GMAIL SERVICE")

    service = get_gmail_service()

    debug("GMAIL SERVICE READY")

    draft_id = create_draft(service, to, subject, message)

    debug("RESPONSE SENT", {"draft_id": draft_id})

    return {"status": "ok", "draft_id": draft_id}
