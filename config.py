SQL_SERVER_CONN = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost\\SQLEXPRESS;"
    "DATABASE=InsuranceAppDB;"
    "Trusted_Connection=yes;"
)

UPLOAD_FOLDER = "uploads"
CHROMA_DIR = "chroma_db"

EMBED_MODEL = "all-minilm"
LLM_MODEL = "llama2"
SIMILARITY_THRESHOLD = 0.75
TOP_K = 5

# Secret key for session management (replace with a fixed secret in production)
SECRET_KEY = "insurance-app-secret-key-change-in-production"