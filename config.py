SQL_SERVER_CONN = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost\\SQLEXPRESS;"
    "DATABASE=InsuranceAppDB;"
    "Trusted_Connection=yes;"
)

UPLOAD_FOLDER = "uploads"
CHROMA_DIR = "chroma_db"

EMBED_MODEL = "all-minilm"
EMBED_WORKERS = 6
LLM_MODEL = "mistral"
SIMILARITY_THRESHOLD = 0.75
TOP_K = 2

# Secret key for session management (replace with a fixed secret in production)
SECRET_KEY = "insurance-app-secret-key-change-in-production"
