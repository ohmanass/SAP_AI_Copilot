# SAP AI Copilot

Assistant IA pour SAP MM et le développement ABAP, construit sur **SAP Generative AI Hub** (SAP AI Core). Il combine un agent à **Tool Calling** qui interroge des données d'achat, un pipeline **RAG** sur une documentation technique SAP et une interface **SAPUI5** inspirée de SAP Fiori.

![Interface de conversation](docs/screenshots/conversation.png)

## Fonctionnalités

### Agent IA

- GPT-4o-mini via SAP Generative AI Hub (SDK `sap-ai-sdk-gen`)
- Tool Calling avec trois outils : consultation d'une commande d'achat, analyse des retards de livraison, recherche dans la documentation
- RAG sur ChromaDB avec les embeddings `text-embedding-3-large`
- Réponses fondées sur les données et les documents récupérés, avec citation des sources

### Interface SAPUI5

- Réponses en streaming : l'outil en cours d'exécution s'affiche en direct (« Reading purchase order data », « Searching the knowledge base »), puis le texte apparaît progressivement. Un bouton **Stop** interrompt la génération sans rien enregistrer
- Chat : envoi avec Entrée, retour à la ligne avec Shift + Entrée, indicateur de génération, rendu Markdown, copie des réponses, gestion des erreurs avec bouton « Réessayer »
- Sources RAG affichées sous chaque réponse, avec l'extrait réellement récupéré dans ChromaDB et sa distance vectorielle
- Outils utilisés par l'agent affichés pour chaque réponse
- Historique persistant : nouvelle conversation, liste, reprise, suppression. Le contexte de l'agent est restauré après un redémarrage du backend
- Page **Knowledge Base** : documents indexés, nombre de chunks indexés et attendus, statut d'indexation
- Page **Settings** : statut en direct du backend, de l'IA (avec test de connexion), de la base vectorielle et des données SAP
- Thème sombre bleu nuit basé sur `sap_horizon_dark`, responsive desktop et tablette

| Accueil | Sources RAG |
| --- | --- |
| ![Accueil](docs/screenshots/accueil.png) | ![Sources RAG](docs/screenshots/sources-rag.png) |

| Knowledge Base | Settings |
| --- | --- |
| ![Knowledge Base](docs/screenshots/knowledge-base.png) | ![Settings](docs/screenshots/settings.png) |

## Architecture

```text
Frontend SAPUI5 (UI5 Tooling, port 8080)
        │  HTTP / REST (JSON)
        ▼
Backend FastAPI (port 8000)
        │
        ▼
Agent SAP (Tool Calling)
        ├── SAP Generative AI Hub ── GPT-4o-mini, text-embedding-3-large
        ├── get_purchase_order        ─┐
        ├── get_overdue_orders         ├── SQLite (données SAP MM fictives)
        └── search_sap_documentation ── RAG / ChromaDB
```

```text
SAP_AI_Copilot/
├── app/
│   ├── api.py              # API FastAPI
│   ├── system_info.py      # Statut des services et inventaire de la Knowledge Base
│   ├── agent/agent.py      # Agent à Tool Calling
│   ├── llm/                # Client SAP Generative AI Hub
│   ├── tools/              # Outils appelés par l'agent
│   ├── rag/                # Découpage, indexation, recherche (ChromaDB)
│   ├── database/           # Schéma et jeu de données SQLite
│   └── storage/            # Persistance de l'historique des conversations
├── data/documents/         # Documentation indexée par le RAG
├── frontend/
│   ├── ui5.yaml            # UI5 Tooling (SAPUI5 1.136 LTS)
│   └── webapp/
│       ├── controller/     # App, Chat, KnowledgeBase, Settings
│       ├── view/           # Vues XML
│       ├── service/        # Client REST centralisé
│       ├── util/           # Rendu Markdown sécurisé
│       ├── css/            # Thème
│       └── i18n/           # Textes
└── requirements.txt
```

## API

| Méthode | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Disponibilité du backend |
| `POST` | `/api/chat` | Envoie un message. Renvoie `session_id`, `response`, `sources` et `tools_used` |
| `POST` | `/api/chat/stream` | Même requête, réponse en flux NDJSON : `session`, `tool_start`, `tool_end`, `token`, `done`, `error` |
| `GET` | `/api/sessions` | Liste des conversations |
| `GET` | `/api/sessions/{id}` | Messages d'une conversation |
| `DELETE` | `/api/sessions/{id}` | Supprime une conversation |
| `GET` | `/api/knowledge-base` | Documents, chunks indexés et statut |
| `GET` | `/api/status?check_ai=true` | Statut du backend, de l'IA, de ChromaDB et de SQLite |

Exemple :

```json
POST /api/chat
{ "message": "Explique la différence entre EKKO et EKPO", "session_id": null }
```

```json
{
  "session_id": "6ad50f57-…",
  "response": "EKKO contient l'en-tête de la commande d'achat…",
  "sources": [{ "source": "sap_mm.txt", "chunk": 0, "distance": 0.977, "content": "…" }],
  "tools_used": ["search_sap_documentation"]
}
```

Exemple de flux renvoyé par `/api/chat/stream` (une ligne JSON par événement) :

```text
{"type": "session", "session_id": "0782671e-…"}
{"type": "tool_start", "name": "get_purchase_order"}
{"type": "tool_end", "name": "get_purchase_order", "success": true}
{"type": "token", "content": "Purchase order"}
{"type": "done", "session_id": "0782671e-…", "response": "…", "sources": [], "tools_used": ["get_purchase_order"]}
```

> SAP Generative AI Hub renvoie le premier token puis le reste du texte d'un seul bloc (mise en mémoire tampon côté SAP AI Core). Le frontend affiche donc ce texte reçu de façon progressive. Les événements d'outils, eux, arrivent bien en temps réel.

La documentation interactive de l'API est disponible sur `http://127.0.0.1:8000/docs`.

## Installation

### Prérequis

- Python 3.12
- Node.js 20.11 ou plus récent
- Un accès à SAP AI Core avec des déploiements `gpt-4o-mini` et `text-embedding-3-large` dans SAP Generative AI Hub

### Backend

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Identifiants SAP AI Core : copier le modèle puis le compléter
Copy-Item .env.example .env

# Données SAP MM fictives (SQLite)
python -m app.database.seed

# Indexation de la documentation dans ChromaDB (appelle le modèle d'embeddings)
python -m app.rag.index_documents

uvicorn app.api:app --reload --port 8000
```

### Frontend

```powershell
cd frontend
npm install
npm start
```

L'application s'ouvre sur `http://localhost:8080/index.html`.

> Sous Windows, si PowerShell bloque `npm` (politique d'exécution), utiliser `npm.cmd`.

### Commandes utiles

| Commande | Rôle |
| --- | --- |
| `npm start` | Serveur de développement UI5 sur le port 8080 |
| `npm run lint` | Analyse du code SAPUI5 (UI5 linter) |
| `npm run build` | Build de production dans `frontend/dist` |
| `python -m app.main` | Version en ligne de commande de l'agent |

## Sécurité

- Les identifiants SAP AI Core restent côté serveur dans `.env`, exclu du dépôt.
- Le navigateur ne reçoit que des noms de modèles, des compteurs et des indicateurs de statut.
- Les réponses du modèle sont échappées avant le rendu Markdown, puis nettoyées par le sanitizer HTML de SAPUI5.
- CORS limité à `localhost:8080`.

## Données

Les commandes d'achat, fournisseurs et articles de la base SQLite sont **fictifs**. Ils sont générés par `app/database/seed.py` à des fins de démonstration. Les documents de `data/documents` servent de base documentaire au RAG.

## Pistes d'évolution

- Affichage des données SAP sous forme de tableaux Fiori
- Import et ré-indexation de documents depuis l'interface
- Connexion à une API OData S/4HANA à la place de SQLite
- Déploiement sur SAP BTP (Cloud Foundry, XSUAA)

## Stack technique

SAPUI5 1.136 · UI5 Tooling 4 · FastAPI · SAP Generative AI Hub · SAP AI SDK · ChromaDB · SQLite · Python 3.12
