import pyodbc
import numpy as np
import hashlib
from config import SQL_SERVER_CONN


def get_conn():
    """Create and return database connection"""
    return pyodbc.connect(SQL_SERVER_CONN)


def init_db():
    """Initialize database tables if they don't exist"""
    conn = get_conn()
    cursor = conn.cursor()

    try:
        # Users table
        cursor.execute('''
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='users' AND xtype='U')
            CREATE TABLE users (
                user_id INT IDENTITY(1,1) PRIMARY KEY,
                username NVARCHAR(100) UNIQUE NOT NULL,
                password_hash NVARCHAR(255) NOT NULL,
                created_at DATETIME DEFAULT GETDATE()
            )
        ''')

        # Documents table — each document is private to the uploading user
        cursor.execute('''
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='documents' AND xtype='U')
            CREATE TABLE documents (
                doc_id INT IDENTITY(1,1) PRIMARY KEY,
                user_id INT NOT NULL,
                username NVARCHAR(100),
                filename NVARCHAR(255) NOT NULL,
                original_filename NVARCHAR(255),
                file_path NVARCHAR(500),
                upload_date DATETIME DEFAULT GETDATE(),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

        # Questions table — each question is private to the asking user
        cursor.execute('''
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='questions' AND xtype='U')
            CREATE TABLE questions (
                question_id INT IDENTITY(1,1) PRIMARY KEY,
                user_id INT NOT NULL,
                username NVARCHAR(100),
                question_text NVARCHAR(MAX) NOT NULL,
                embedding VARBINARY(MAX),
                asked_date DATETIME DEFAULT GETDATE(),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        ''')

        # Answers table — linked to questions (inherently private by join)
        cursor.execute('''
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='answers' AND xtype='U')
            CREATE TABLE answers (
                answer_id INT IDENTITY(1,1) PRIMARY KEY,
                question_id INT NOT NULL,
                verdict NVARCHAR(20),          -- APPROVED / DENIED / PARTIAL / UNCLEAR
                answer_text NVARCHAR(MAX),
                clause_reference NVARCHAR(MAX),
                source_doc NVARCHAR(255),
                source_page INT,
                confidence_score FLOAT,
                is_from_cache BIT DEFAULT 0,
                created_at DATETIME DEFAULT GETDATE(),
                FOREIGN KEY (question_id) REFERENCES questions(question_id)
            )
        ''')

        conn.commit()
        print("Database initialized successfully")

    except Exception as e:
        print(f"Error initializing database: {e}")
        import traceback
        traceback.print_exc()
        conn.rollback()
    finally:
        conn.close()


# ── Auth helpers ──────────────────────────────────────────────────────────────

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def register_user(username, password):
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            username, hash_password(password)
        )
        conn.commit()
        conn.close()
        return True, "Registration successful"
    except pyodbc.IntegrityError:
        return False, "Username already exists"
    except Exception as e:
        return False, f"Registration failed: {str(e)}"


def verify_user(username, password):
    try:
        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id FROM users WHERE username = ? AND password_hash = ?",
            username, hash_password(password)
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"Login error: {e}")
        return None


# ── Document helpers ──────────────────────────────────────────────────────────

def update_answer(answer_id, new_explanation, new_verdict):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE answers 
        SET answer = ?, verdict = ? 
        WHERE answer_id = ?
    """, (new_explanation, new_verdict, answer_id))
    conn.commit()
    conn.close()

def save_document(user_id, username, filename, original_filename, filepath):
    """Save document metadata linked to this user only"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO documents (user_id, username, filename, original_filename, file_path)
           VALUES (?, ?, ?, ?, ?)""",
        user_id, username, filename, original_filename, filepath
    )
    conn.commit()
    conn.close()


def get_user_documents(user_id):
    """Return only documents uploaded by this user"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT doc_id, filename, original_filename, upload_date
           FROM documents WHERE user_id = ? ORDER BY upload_date DESC""",
        user_id
    )
    docs = []
    for row in cursor.fetchall():
        docs.append({
            'doc_id': row[0],
            'filename': row[1],
            'original_filename': row[2],
            'upload_date': str(row[3])
        })
    conn.close()
    return docs


def check_document_exists_for_user(user_id, filename):
    """Check if this exact filename was already uploaded by this user"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM documents WHERE user_id = ? AND filename = ?",
        user_id, filename
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count > 0


def delete_document(user_id, doc_id):
    """Delete a document belonging to the user"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM documents WHERE doc_id = ? AND user_id = ?",
        doc_id, user_id
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted > 0


# ── Q&A helpers ───────────────────────────────────────────────────────────────

def save_question(user_id, username, question_text, embedding_array):
    """Save a question; returns new question_id"""
    conn = get_conn()
    cursor = conn.cursor()
    emb_bytes = embedding_array.tobytes()
    cursor.execute(
        """INSERT INTO questions (user_id, username, question_text, embedding)
           OUTPUT INSERTED.question_id
           VALUES (?, ?, ?, ?)""",
        user_id, username, question_text, emb_bytes
    )
    qid = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return qid


def save_answer(question_id, verdict, answer_text, clause_reference,
                source_doc, source_page, confidence, is_from_cache=False):
    """Save an answer; returns new answer_id"""
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO answers
               (question_id, verdict, answer_text, clause_reference,
                source_doc, source_page, confidence_score, is_from_cache)
           OUTPUT INSERTED.answer_id
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        question_id, verdict, answer_text, clause_reference,
        source_doc, source_page, confidence, is_from_cache
    )
    aid = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return aid


def fetch_user_questions_with_answers(user_id):
    """
    Return all questions + answers for this user only.
    Returns list of tuples:
    (question_id, question_text, embedding_bytes, answer_text, verdict,
     clause_reference, confidence, source_doc, source_page, answer_id)
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """SELECT
               q.question_id,
               q.question_text,
               q.embedding,
               a.answer_text,
               a.verdict,
               a.clause_reference,
               a.confidence_score,
               a.source_doc,
               a.source_page,
               a.answer_id
           FROM questions q
           LEFT JOIN answers a ON q.question_id = a.question_id
           WHERE q.user_id = ?
           ORDER BY q.asked_date DESC""",
        user_id
    )
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_user_history(user_id):
    """Return formatted history for this user only"""
    rows = fetch_user_questions_with_answers(user_id)
    history = []
    for row in rows:
        history.append({
            'question_id': row[0],
            'question': row[1],
            'answer': row[3] or 'No answer yet',
            'verdict': row[4] or 'UNCLEAR',
            'clause_reference': row[5] or '',
            'confidence': float(row[6]) if row[6] is not None else 0.0,
            'source_doc': row[7] or 'N/A',
            'source_page': row[8] or 0,
            'answer_id': row[9]
        })
    return history


def get_statistics(user_id):
    """Per-user statistics"""
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM documents WHERE user_id = ?", user_id)
    total_docs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM questions WHERE user_id = ?", user_id)
    total_questions = cursor.fetchone()[0]

    cursor.execute(
        """SELECT COUNT(*) FROM answers a
           JOIN questions q ON a.question_id = q.question_id
           WHERE q.user_id = ?""", user_id)
    total_answers = cursor.fetchone()[0]

    conn.close()
    return {
        'total_docs': total_docs,
        'total_questions': total_questions,
        'total_answers': total_answers
    }
