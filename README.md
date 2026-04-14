# InsuranceIQ — AI-Powered Insurance Claim Analyser

A Flask-based RAG (Retrieval-Augmented Generation) application that lets users upload their insurance policy PDFs and query them for claim eligibility, with the AI citing the exact policy clause.

---

## Features

| Feature | Details |
|---|---|
| **User isolation** | Each user's documents, queries, and vector store are completely private |
| **Claim verdict** | Responses are classified as APPROVED / DENIED / PARTIAL / UNCLEAR |
| **Clause citation** | Exact policy clause or section quoted alongside every answer |
| **Semantic caching** | If you ask a similar question twice, the cached answer is returned instantly |
| **Session auth** | Flask server-side session with password hashing (SHA-256) |
| **Export** | Download your entire query history as Excel |

---

## Project Structure

```
insurance_app/
├── app.py               # Flask routes & background PDF processing
├── db.py                # SQL Server database helpers (user-isolated queries)
├── llm_utils.py         # Per-user Chroma vector stores + insurance LLM prompt
├── config.py            # Connection strings & model config
├── requirements.txt
├── templates/
│   ├── login.html
│   └── index.html
└── static/
    ├── style.css
    └── app.js
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Install Ollama models
```bash
ollama pull llama2
ollama pull nomic-embed-text
```

### 3. Configure database
Edit `config.py` to point to your SQL Server instance:
```python
SQL_SERVER_CONN = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost\\SQLEXPRESS;"
    "DATABASE=InsuranceAppDB;"
    "Trusted_Connection=yes;"
)
```

Create the database in SQL Server:
```sql
CREATE DATABASE InsuranceAppDB;
```

Tables are created automatically on first run.

### 4. Run
```bash
python app.py
```
Visit `http://localhost:5000`

---

## How It Works

1. **Upload** your insurance policy PDF (stored in your private vector store).
2. **Ask** a claim question like: *"56-year-old male, knee surgery, 36 months of coverage — is this covered?"*
3. The app retrieves the most relevant policy sections using semantic search.
4. The LLM reads the retrieved clauses and returns a structured verdict.
5. Response shows: **Verdict badge** → **Exact clause** → **Full explanation** → **Source page**.

---

## Key Design Decisions

### User Data Isolation
- Each user gets their own Chroma collection at `chroma_db/user_{id}/`
- Documents table is filtered by `user_id` on every query
- Questions and answers are linked to `user_id` — no cross-user leakage

### Insurance-Specific Prompt
The LLM is given a strict prompt that forces it to:
- Return a structured `VERDICT / CLAUSE / EXPLANATION` response
- Base its verdict only on the retrieved policy text
- Quote the exact clause text

### Semantic Caching
Before hitting the LLM, similar past questions (cosine similarity ≥ 0.75) are returned from the database, saving LLM inference time.

---

## Changing the LLM
Edit `config.py`:
```python
LLM_MODEL = "llama2"       # or "mistral", "llama3", etc.
EMBED_MODEL = "nomic-embed-text"
```
