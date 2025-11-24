import base64
import os
import time
import json
import traceback
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage

from flask import Flask, request, jsonify
import requests

import google.auth
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from google.cloud import firestore


# Gmail impersonation scopes
SCOPES = ["https://mail.google.com/"]

# Workspace user to impersonate
GMAIL_USER = os.environ.get("GMAIL_USER")

# Service account running on Cloud Run (same SA configurée pour Domain-wide Delegation)
SA_EMAIL = os.environ.get("GOOGLE_SERVICE_ACCOUNT_EMAIL")

# Tracking pixel service base URL (autre Cloud Run)
# Exemple: https://email-open-tracker-xxxx.a.run.app
PIXEL_TRACKER_BASE_URL = os.environ.get("PIXEL_TRACKER_BASE_URL", "").rstrip("/")

# Nom de la collection Firestore pour les ouvertures
PIXEL_COLLECTION = os.environ.get("PIXEL_COLLECTION", "email_opens")

# Nom de la collection Firestore pour les drafts en attente de review
DRAFT_COLLECTION = os.environ.get("DRAFT_COLLECTION", "email_drafts")

# Activer/désactiver le tracking d'email
# true = ajoute le pixel de tracking, false = pas de tracking
ENABLE_TRACKING = os.environ.get("ENABLE_TRACKING", "false").lower() in ("true", "1", "yes")

# Mode d'envoi : "draft" ou "send"
# draft = crée un brouillon
# send = envoie directement l'email
SEND_MODE = os.environ.get("SEND_MODE", "draft").lower()

# Firestore client
db = firestore.Client()

# --- Flask app -------------------------------------------------------

app = Flask(__name__)


# --- Utils -----------------------------------------------------------

def debug(msg, data=None):
    print("\n────────── DEBUG ──────────")
    print(msg)
    if data is not None:
        try:
            print(json.dumps(data, indent=2, default=str))
        except Exception:
            print(str(data))
    print("────────────────────────────\n")


def now_utc():
    return datetime.now(timezone.utc)


# --- Step 1: Call IAMCredentials API: signJwt ------------------------

def sign_jwt_with_iam(payload: dict) -> str:
    """Signs a JWT using the IAMCredentials API for the Cloud Run service account."""
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
    """Builds an impersonated Gmail service using domain-wide delegation."""
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


# --- Signature helpers ------------------------------------------------

def get_user_signature(service):
    """Récupère la signature Gmail (HTML) de l'utilisateur impersoné."""
    try:
        send_as = service.users().settings().sendAs().get(
            userId="me",
            sendAsEmail=GMAIL_USER,
        ).execute()

        signature = send_as.get("signature", "")
        
        # Ajouter alt="" aux images de la signature qui n'en ont pas
        if signature and "<img" in signature:
            import re
            # Ajouter alt="" aux balises img qui n'ont pas déjà un attribut alt
            signature = re.sub(
                r'<img(?![^>]*\balt\s*=)([^>]*)>',
                r'<img alt=""\1>',
                signature,
                flags=re.IGNORECASE
            )
        
        debug("SIGNATURE RETRIEVED", {"signature_length": len(signature)})
        return signature
    except Exception as e:
        debug("ERROR GETTING SIGNATURE", str(e))
        return ""


# --- Step 3: Create Gmail Draft or Send + tracking pixel -------------

def save_draft_to_firestore(to, subject, body, x_external_id=""):
    """
    Sauvegarde un draft dans Firestore pour review humain.
    
    Returns:
        draft_id: ID du document Firestore créé
    """
    draft_id = str(uuid.uuid4())
    
    try:
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        draft_data = {
            "to": to,
            "subject": subject,
            "body": body,
            "created_at": now_utc(),
            "status": "pending",  # pending, sent, rejected
        }
        
        # Ajouter x_external_id s'il est fourni
        if x_external_id:
            draft_data["x_external_id"] = x_external_id
        
        doc_ref.set(draft_data)
        debug("DRAFT SAVED TO FIRESTORE", {"draft_id": draft_id, "x_external_id": x_external_id})
        return draft_id
    except Exception as e:
        debug("ERROR SAVING DRAFT TO FIRESTORE", str(e))
        raise


def create_or_send_email(service, to, subject, body, mode="draft", x_external_id=""):
    """
    Crée un brouillon Firestore OU envoie directement l'email en HTML :
    - mode="draft": sauvegarde dans Firestore pour review humain
    - mode="send": envoie directement avec pixel de tracking et signature
    
    Args:
        mode: "draft" pour sauvegarder dans Firestore, "send" pour envoyer directement
        x_external_id: ID externe (ex: pharowCompanyId) pour tracking
    """
    debug("CREATE_OR_SEND_EMAIL INPUT", {
        "to": to,
        "subject": subject,
        "body_len": len(body),
        "mode": mode,
        "x_external_id": x_external_id,
    })

    # En mode draft, on sauvegarde dans Firestore au lieu de créer un draft Gmail
    if mode == "draft":
        draft_id = save_draft_to_firestore(to, subject, body, x_external_id)
        return draft_id, None

    # Générer un ID unique pour le pixel
    pixel_id = str(uuid.uuid4())
    tracking_url = None

    if ENABLE_TRACKING and PIXEL_TRACKER_BASE_URL:
        tracking_url = f"{PIXEL_TRACKER_BASE_URL}/pixel?id={pixel_id}"
        debug("TRACKING URL", tracking_url)
    else:
        debug("TRACKING DISABLED OR NO PIXEL_TRACKER_BASE_URL", {"enabled": ENABLE_TRACKING, "url": bool(PIXEL_TRACKER_BASE_URL)})

    # Pré-créer le doc Firestore pour ce pixel (pour tracer plus tard)
    if ENABLE_TRACKING:
        try:
            doc_ref = db.collection(PIXEL_COLLECTION).document(pixel_id)
            doc_ref.set(
                {
                    "to": to,
                    "subject": subject,
                    "open_count": 0,
                    "created_at": now_utc(),
                },
                merge=True,
            )
            debug("FIRESTORE DOC CREATED", {"pixel_id": pixel_id})
        except Exception as e:
            debug("ERROR WRITING FIRESTORE", str(e))

    # Récupérer la signature HTML configurée dans Gmail
    signature_html = get_user_signature(service)

    # Fallback texte brut (pour les clients qui ne lisent pas le HTML)
    plain_body = body
    if signature_html:
        plain_body = f"{body}\n\n-- \nSignature"

    # Corps HTML : on remplace les sauts de ligne par des <br> simples
    html_body = body.replace("\n", "<br>")

    # Pixel de tracking (si URL dispo)
    tracking_img = ""
    if tracking_url:
        tracking_img = (
            f'<img src="{tracking_url}" '
            f'width="1" height="1" alt="" style="display:none;">'
        )

    # Construction final HTML :
    # texte + pixel (dans le même bloc) + signature en-dessous
    if signature_html:
        html_body = f"<div>{html_body}{tracking_img}</div><br>{signature_html}"
    else:
        html_body = f"<div>{html_body}{tracking_img}</div>"

    # Construction du message multi-part (texte + HTML)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject

    # Partie texte (fallback)
    msg.set_content(plain_body)

    # Partie HTML (principale pour les clients modernes)
    msg.add_alternative(html_body, subtype="html")

    # Encodage pour l'API Gmail
    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    debug("MESSAGE RAW (FIRST 200 CHARS)", encoded[:200])

    # Mode send: envoi direct du message
    message_body = {"raw": encoded}
    sent = service.users().messages().send(
        userId="me",
        body=message_body,
    ).execute()
    debug("MESSAGE SENT RESPONSE", sent)
    return sent.get("id"), pixel_id


# --- HTTP endpoint (Cloud Run) ---------------------------------------

@app.route("/", methods=["POST"])
def root():
    """
    Endpoint HTTP Cloud Run.

    Attend un JSON du type:
    {
      "to": "client@exemple.fr",
      "subject": "Sujet du mail",
      "message": "Corps du message en texte brut",
      "mode": "draft" ou "send" (optionnel, par défaut utilise SEND_MODE env var)
    }

    Mode "draft": sauvegarde dans Firestore pour review humain
    Mode "send": envoie directement l'email avec signature et pixel de tracking

    Réponse:
    {
      "status": "ok",
      "mode": "draft" ou "send",
      "id": "..." (draft_id Firestore ou message_id Gmail),
      "pixel_id": "..." (uniquement pour mode send)
    }
    """
    debug("REQUEST RECEIVED")

    try:
        data = request.get_json(silent=True) or {}
        debug("REQUEST JSON", data)

        to = data.get("to", "client@exemple.fr")
        subject = data.get("subject", "Email automatique")
        message = data.get("message", "Message automatique.")
        x_external_id = data.get("x_external_id", "")
        
        # Mode : utilise celui du payload, sinon celui de la variable d'env
        mode = data.get("mode", SEND_MODE).lower()
        if mode not in ["draft", "send"]:
            mode = "draft"

        # En mode draft, pas besoin du service Gmail
        if mode == "draft":
            draft_id = save_draft_to_firestore(to, subject, message, x_external_id)
            debug("DRAFT SAVED", {"draft_id": draft_id, "x_external_id": x_external_id})
            return jsonify(
                {"status": "ok", "mode": "draft", "draft_id": draft_id}
            ), 200
        
        # Mode send: on a besoin du service Gmail
        debug("GETTING GMAIL SERVICE")
        service = get_gmail_service()
        debug("GMAIL SERVICE READY")

        email_id, pixel_id = create_or_send_email(service, to, subject, message, mode, x_external_id)
        debug("RESPONSE SENT", {"mode": mode, "id": email_id, "pixel_id": pixel_id})

        return jsonify(
            {"status": "ok", "mode": mode, "id": email_id, "pixel_id": pixel_id}
        ), 200

    except Exception as e:
        debug("UNCAUGHT ERROR", {
            "error": str(e),
            "traceback": traceback.format_exc(),
        })
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/send-draft", methods=["POST"])
def send_draft():
    """
    Endpoint pour récupérer un draft Firestore et l'envoyer.

    Attend un JSON du type:
    {
      "draft_id": "uuid-du-draft"
    }

    Récupère le draft dans Firestore, l'envoie avec le pixel de tracking,
    et met à jour le statut dans Firestore.

    Réponse:
    {
      "status": "ok",
      "message_id": "..." (ID du message Gmail envoyé),
      "pixel_id": "..." (ID du pixel de tracking)
    }
    """
    debug("SEND DRAFT REQUEST RECEIVED")

    try:
        data = request.get_json(silent=True) or {}
        debug("REQUEST JSON", data)

        draft_id = data.get("draft_id")
        if not draft_id:
            return jsonify({"status": "error", "error": "draft_id is required"}), 400

        # Récupérer le draft depuis Firestore
        doc_ref = db.collection(DRAFT_COLLECTION).document(draft_id)
        doc = doc_ref.get()
        
        if not doc.exists:
            return jsonify({"status": "error", "error": "Draft not found"}), 404
        
        draft_data = doc.to_dict()
        debug("DRAFT DATA RETRIEVED", draft_data)
        
        # Vérifier le statut
        if draft_data.get("status") == "sent":
            return jsonify({"status": "error", "error": "Draft already sent"}), 400
        
        to = draft_data.get("to")
        subject = draft_data.get("subject")
        body = draft_data.get("body")
        
        # Obtenir le service Gmail
        debug("GETTING GMAIL SERVICE")
        service = get_gmail_service()
        debug("GMAIL SERVICE READY")
        
        # Envoyer l'email avec le pixel de tracking
        message_id, pixel_id = create_or_send_email(service, to, subject, body, mode="send")
        
        # Mettre à jour le statut dans Firestore
        doc_ref.update({
            "status": "sent",
            "sent_at": now_utc(),
            "message_id": message_id,
            "pixel_id": pixel_id,
        })
        
        debug("DRAFT SENT", {"message_id": message_id, "pixel_id": pixel_id})
        
        return jsonify({
            "status": "ok",
            "message_id": message_id,
            "pixel_id": pixel_id,
        }), 200

    except Exception as e:
        debug("UNCAUGHT ERROR", {
            "error": str(e),
            "traceback": traceback.format_exc(),
        })
        return jsonify({"status": "error", "error": str(e)}), 500
