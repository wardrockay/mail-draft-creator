# **README – Gmail Email Service via Cloud Run & Domain-Wide Delegation**

Ce service Cloud Run permet de **gérer l'envoi d'emails Gmail** au nom d'un utilisateur Google Workspace, **sans fichier JSON**, en utilisant :

* le **service account Cloud Run**
* l'API **IAMCredentials.signJwt**
* la **Domain-Wide Delegation (DWD)**
* les API Gmail
* **Firestore** pour stocker les drafts en attente de review

Il expose deux endpoints HTTP :
- Un pour créer des drafts (sauvegardés dans Firestore) ou envoyer directement
- Un pour récupérer un draft Firestore et l'envoyer avec tracking

---

## 🚀 Fonctionnalités

* **Mode draft** : Sauvegarde dans Firestore pour review humain (plus de draft Gmail)
* **Mode send** : Envoi direct avec pixel de tracking et signature Gmail
* Endpoint dédié pour récupérer un draft Firestore et l'envoyer
* Impersonation d'un utilisateur Workspace (DWD)
* Aucun téléchargement de clé JSON
* Auth Cloud Run 100% gérée côté Google
* Debug détaillé intégré
* Déploiement simple sur Cloud Run (Python Functions Framework)

---

# 1. 📦 Prérequis

### Côté Google Cloud Platform

* Un projet GCP
* Un service account dédié (ex : `prospector@project.iam.gserviceaccount.com`)
* Ce service account doit avoir :

  * `roles/iam.serviceAccountTokenCreator`
  * Et être **assigné au service Cloud Run**

### Côté Google Workspace (Admin Console)

* Domain-Wide Delegation activée pour ce service account
* Dans *Security → API Controls → Domain-wide delegation* :

  * Ajouter le **Client ID** du service account
  * Ajouter le scope :

```
https://mail.google.com/
```

### Côté Cloud Run

* Variables d'environnement :

```
GMAIL_USER=adresse_utilisateur@tondomaine.fr
GOOGLE_SERVICE_ACCOUNT_EMAIL=prospector@tonprojet.iam.gserviceaccount.com
PIXEL_TRACKER_BASE_URL=https://email-open-tracker-xxxx.a.run.app
ENABLE_TRACKING=true
SEND_MODE=draft (draft ou send - par défaut)
PIXEL_COLLECTION=email_opens (nom collection Firestore pour tracking)
DRAFT_COLLECTION=email_drafts (nom collection Firestore pour drafts)
```

* Le service doit être exécuté **avec le même service account**.

### Dependencies (requirements.txt)

```
google-auth
google-auth-httplib2
google-api-python-client
google-cloud-firestore
requests
functions-framework
flask
```

---

# 2. ⚙️ Déploiement Cloud Run

### Déploiement direct :

```
gcloud run deploy draft-creator \
  --source . \
  --region europe-west1 \
  --allow-unauthenticated \
  --service-account prospector@tonprojet.iam.gserviceaccount.com
```

Ou avec authentification privée selon ton usage.

---

# 3. 🧠 Comment ça marche

Le code suit ces étapes principales :

### 1) Récupère le token du service account Cloud Run

```python
creds, _ = google.auth.default()
creds.refresh(GoogleRequest())
access_token = creds.token
```

### 2) Appelle IAMCredentials → `signJwt`

Google signe un JWT incluant `sub=GMAIL_USER`.

### 3) Échange le JWT signé contre un token OAuth2

Ce token permet d'appeler Gmail **au nom de l'utilisateur impersonné**.

### 4) Sauvegarde ou envoi

En mode **draft**, sauvegarde dans Firestore pour review humain.
En mode **send**, envoie l'email avec signature et pixel de tracking.

---

# 4. 📨 Utilisation de l'API HTTP

## Endpoint principal : Créer un draft ou envoyer

### Endpoint

```
POST / (Cloud Run URL)
Content-Type: application/json
```

### Payload attendu :

```json
{
  "to": "client@example.com",
  "subject": "Hello",
  "message": "Ceci est un message",
  "mode": "draft"
}
```

### Réponse en mode `draft` :

```json
{
  "status": "ok",
  "mode": "draft",
  "draft_id": "uuid-du-draft-firestore"
}
```

Le draft est **sauvegardé dans Firestore** (collection `email_drafts`) pour review humain.

### Réponse en mode `send` :

```json
{
  "status": "ok",
  "mode": "send",
  "id": "message-id-gmail",
  "pixel_id": "uuid-pixel-tracking"
}
```

L'email est **envoyé directement** avec signature et pixel de tracking.

---

## Endpoint secondaire : Envoyer un draft

Une fois qu'un draft a été validé, utilisez cet endpoint pour l'envoyer :

### Endpoint

```
POST /send-draft (Cloud Run URL)
Content-Type: application/json
```

### Payload attendu :

```json
{
  "draft_id": "uuid-du-draft-firestore"
}
```

### Réponse

```json
{
  "status": "ok",
  "message_id": "message-id-gmail",
  "pixel_id": "uuid-pixel-tracking"
}
```

Le draft est récupéré depuis Firestore, envoyé avec le **pixel de tracking** et la **signature Gmail**, puis son statut est mis à jour dans Firestore (`status: "sent"`).

---

# 5. 🧪 Exemples d'appels via curl

### Créer un draft pour review

```bash
curl -X POST "https://ton-service.run.app" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "test@example.com",
    "subject": "Test Draft",
    "message": "Ceci est un draft pour review.",
    "mode": "draft"
  }'
```

### Envoyer directement un email

```bash
curl -X POST "https://ton-service.run.app" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "test@example.com",
    "subject": "Email Direct",
    "message": "Ceci est envoyé directement.",
    "mode": "send"
  }'
```

### Envoyer un draft après validation

```bash
curl -X POST "https://ton-service.run.app/send-draft" \
  -H "Content-Type: application/json" \
  -d '{
    "draft_id": "uuid-du-draft"
  }'
```

---

# 6. 🗄️ Structure Firestore

## Collection `email_drafts`

Chaque document représente un draft en attente de review :

```json
{
  "to": "client@example.com",
  "subject": "Sujet du mail",
  "body": "Corps du message",
  "created_at": "2024-01-01T10:00:00Z",
  "status": "pending",
  "sent_at": "2024-01-01T11:00:00Z",
  "message_id": "gmail-message-id",
  "pixel_id": "uuid-pixel"
}
```

**Statuts possibles** :
- `pending` : En attente de review
- `sent` : Envoyé
- `rejected` : Rejeté (à implémenter selon besoins)

## Collection `email_opens`

Chaque document représente un pixel de tracking :

```json
{
  "to": "client@example.com",
  "subject": "Sujet du mail",
  "open_count": 2,
  "created_at": "2024-01-01T10:00:00Z",
  "first_opened_at": "2024-01-01T11:30:00Z",
  "last_opened_at": "2024-01-01T12:00:00Z"
}
```

---

# 7. 🛠 Debug

Le service logge :

* Variables d'environnement utilisées
* Requête envoyée à IAMCredentials
* Réponses IAMCredentials (y compris erreurs)
* Requête et réponse d'échange JWT/token
* Requête Gmail
* Réponse Gmail complète
* Opérations Firestore

En cas d'erreur :

1. Vérifier que Cloud Run utilise le bon service account
2. Vérifier que le service account possède `Service Account Token Creator`
3. Vérifier que son *Client ID* est bien autorisé dans Workspace
4. Vérifier que les scopes DWD correspondent
5. Vérifier que Firestore est activé et accessible

---

# 8. 📁 Structure du code

* `root()` → Endpoint principal : créer draft ou envoyer
* `send_draft()` → Endpoint pour envoyer un draft Firestore
* `get_gmail_service()` → Obtention du client Gmail impersonné
* `sign_jwt_with_iam()` → Signature de JWT par IAMCredentials
* `create_or_send_email()` → Envoi email avec tracking et signature
* `save_draft_to_firestore()` → Sauvegarde draft dans Firestore
* `get_user_signature()` → Récupération signature Gmail HTML

---

# 9. 🔒 Sécurité

* Le service Cloud Run doit être protégé par IAM ou authentification
* Les drafts Firestore doivent avoir des règles de sécurité appropriées
* Le pixel tracker doit être sur un domaine séparé
* Les variables d'environnement sensibles doivent être gérées via Secret Manager
