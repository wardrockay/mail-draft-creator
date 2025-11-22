# **README – Gmail Draft Creator via Cloud Run & Domain-Wide Delegation**

Ce service Cloud Run permet de **créer automatiquement des brouillons Gmail** au nom d’un utilisateur Google Workspace, **sans fichier JSON**, en utilisant :

* le **service account Cloud Run**
* l’API **IAMCredentials.signJwt**
* la **Domain-Wide Delegation (DWD)**
* les API Gmail (brouillons)

Il expose un endpoint HTTP qui prend une charge JSON (`to`, `subject`, `message`) et crée immédiatement un brouillon dans la boîte Gmail de l’utilisateur impersonné.

---

## 🚀 Fonctionnalités

* Création de brouillons Gmail via API
* Impersonation d’un utilisateur Workspace (DWD)
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

* Variables d’environnement :

```
GMAIL_USER=adresse_utilisateur@tondomaine.fr
GOOGLE_SERVICE_ACCOUNT_EMAIL=prospector@tonprojet.iam.gserviceaccount.com
```

* Le service doit être exécuté **avec le même service account**.

### Dependencies (requirements.txt)

```
google-auth
google-auth-httplib2
google-api-python-client
requests
functions-framework
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

Le code suit trois étapes principales :

### 1) Récupère le token du service account Cloud Run

```python
creds, _ = google.auth.default()
creds.refresh(GoogleRequest())
access_token = creds.token
```

### 2) Appelle IAMCredentials → `signJwt`

Google signe un JWT incluant `sub=GMAIL_USER`.

### 3) Échange le JWT signé contre un token OAuth2

Ce token permet d’appeler Gmail **au nom de l'utilisateur impersonné**.

### 4) Envoie la requête Gmail → création d’un brouillon

```python
draft = service.users().drafts().create(...).execute()
```

---

# 4. 📨 Utilisation de l’API HTTP

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
  "message": "Ceci est un brouillon automatique"
}
```

### Réponse

```json
{
  "status": "ok",
  "draft_id": "r88923fe72b2cce64"
}
```

---

# 5. 🧪 Exemple d’appel via curl

```bash
curl -X POST "https://ton-service.run.app" \
  -H "Content-Type: application/json" \
  -d '{
    "to": "test@example.com",
    "subject": "Test Draft",
    "message": "Ceci est un brouillon généré automatiquement."
  }'
```

---

# 6. 🛠 Debug

Le service logge :

* Variables d’environnement utilisées
* Requête envoyée à IAMCredentials
* Réponses IAMCredentials (y compris erreurs)
* Requête et réponse d’échange JWT/token
* Requête Gmail
* Réponse Gmail complète

En cas d’erreur :

1. Vérifier que Cloud Run utilise le bon service account
2. Vérifier que le service account possède `Service Account Token Creator`
3. Vérifier que son *Client ID* est bien autorisé dans Workspace
4. Vérifier que les scopes DWD correspondent

---

# 7. 📁 Structure du code

* `hello_http` → Point d’entrée Cloud Run
* `get_gmail_service()` → Obtention du client Gmail impersonné
* `sign_jwt_with_iam()` → Signature de JWT par IAMCredentials
* `create_draft()` → Création du brouillon Gmail

