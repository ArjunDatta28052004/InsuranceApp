from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, send_file
)
import os
import hashlib
import threading
import time
import io
import pandas as pd
from datetime import datetime
from werkzeug.utils import secure_filename

from config import UPLOAD_FOLDER, SIMILARITY_THRESHOLD, SECRET_KEY
from db import (
    init_db, register_user, verify_user,
    save_document, get_user_documents, check_document_exists_for_user, delete_document,
    save_question, save_answer, fetch_user_questions_with_answers,
    get_user_history, get_statistics, update_answer
)
from llm_utils import (
    process_pdf_to_vectorstore, check_pdf_in_user_vectorstore,
    delete_pdf_from_vectorstore, get_insurance_answer,
    compute_embedding, cosine_sim
)

app = Flask(__name__)
app.secret_key = SECRET_KEY
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

init_db()

# In-memory status trackers
processing_status = {}
processing_lock = threading.Lock()


# ── Background PDF processor ──────────────────────────────────────────────────

def _run_pdf_processing(task_id, user_id, username, filepath, filename, original_filename):
    with processing_lock:
        processing_status[task_id] = {"status": "processing", "progress": 0}

    print(f"[THREAD] Starting: {filename} for user {user_id}")
    try:
        def cb(pct):
            print(f"[THREAD] Progress: {pct}%")
            with processing_lock:
                processing_status[task_id]["progress"] = pct

        cb(5)

        if not check_pdf_in_user_vectorstore(user_id, filename):
            process_pdf_to_vectorstore(user_id, filepath, filename, progress_callback=cb)
        else:
            print("[THREAD] Already indexed, skipping.")
            cb(100)

        if not check_document_exists_for_user(user_id, filename):
            save_document(user_id, username, filename, original_filename, filepath)
            print("[THREAD] Metadata saved.")

        with processing_lock:
            processing_status[task_id] = {"status": "completed", "progress": 100}
        print(f"[THREAD] Complete: {filename}")

    except Exception as e:
        import traceback
        print(f"[THREAD ERROR] {filename} failed:")
        traceback.print_exc()                          # ← this will show the real error
        with processing_lock:
            processing_status[task_id] = {"status": "error", "message": str(e)}

# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("home"))
    return redirect(url_for("login_page"))


@app.route("/login_page")
def login_page():
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def do_login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password required"}), 400

    user_id = verify_user(username, password)
    if user_id:
        session.clear()
        session["user_id"] = user_id
        session["username"] = username
        session.permanent = True
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "Invalid credentials"}), 401


@app.route("/register", methods=["POST"])
def do_register():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "message": "Username and password required"}), 400
    if len(password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters"}), 400

    success, msg = register_user(username, password)
    status = 200 if success else 400
    return jsonify({"success": success, "message": msg}), status


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Main app ──────────────────────────────────────────────────────────────────

@app.route("/home")
def home():
    if "user_id" not in session:
        return redirect(url_for("login_page"))
    return render_template("index.html", username=session["username"])

@app.route("/batch_process", methods=["POST"])
def batch_process():
    if 'user_id' not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    # Store the actual numeric user_id from the session FIRST
    current_user_id = session['user_id'] 
    
    file = request.files.get('excel_file')
    if not file:
        return jsonify({"success": False, "message": "No file uploaded"}), 400

    df = pd.read_excel(file)
    results = []

    for index, row in df.iterrows():
        q_text = row['Question']
        
        # CRITICAL: Ensure you pass 'current_user_id', NOT the loop index or q_text
        verdict, expl, clause, src, pg, conf = get_insurance_answer(current_user_id, q_text)
        results.append({
            "Question": q_text,
            "Verdict": verdict,
            "Clause": clause,
            "Explanation": expl,
            "Source": f"{src} (Pg {pg})"
        })

    output_df = pd.DataFrame(results)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        output_df.to_excel(writer, index=False, sheet_name="Results")
    output.seek(0)
    
    return send_file(output, as_attachment=True, download_name="Batch_Results.xlsx")

@app.route("/edit_answer", methods=["POST"])
def edit_answer():
    data = request.json
    update_answer(data['answer_id'], data['explanation'], data['verdict'])
    return jsonify({"success": True})

# ── Document routes ───────────────────────────────────────────────────────────

@app.route("/upload", methods=["POST"])
def upload_pdf():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    if "file" not in request.files:
        return jsonify({"success": False, "message": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"success": False, "message": "Only PDF files are allowed"}), 400

    user_id = session["user_id"]
    username = session["username"]
    original_filename = file.filename
    safe_name = secure_filename(file.filename)
    # Prefix with user_id to keep files isolated on disk too
    stored_name = f"u{user_id}_{safe_name}"

    if check_document_exists_for_user(user_id, stored_name):
        return jsonify({"success": False, "message": "You have already uploaded this document."}), 400

    filepath = os.path.join(UPLOAD_FOLDER, stored_name)
    file.save(filepath)

    task_id = hashlib.md5(f"{user_id}{stored_name}{time.time()}".encode()).hexdigest()

    t = threading.Thread(
        target=_run_pdf_processing,
        args=(task_id, user_id, username, filepath, stored_name, original_filename),
        daemon=True
    )
    t.start()

    return jsonify({
        "success": True,
        "task_id": task_id,
        "filename": stored_name,
        "original_filename": original_filename
    })


@app.route("/upload_status/<task_id>")
def upload_status(task_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    with processing_lock:
        status = processing_status.get(task_id, {"status": "not_found"})
    return jsonify(status)


@app.route("/documents")
def get_documents():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    docs = get_user_documents(session["user_id"])
    return jsonify({"success": True, "documents": docs})


@app.route("/documents/<int:doc_id>", methods=["DELETE"])
def remove_document(doc_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    user_id = session["user_id"]
    # Get filename before deletion
    docs = get_user_documents(user_id)
    target = next((d for d in docs if d["doc_id"] == doc_id), None)
    if not target:
        return jsonify({"success": False, "message": "Document not found"}), 404

    filename = target["filename"]
    deleted = delete_document(user_id, doc_id)
    if deleted:
        delete_pdf_from_vectorstore(user_id, filename)
        # Optionally remove file from disk
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)
        return jsonify({"success": True, "message": "Document deleted"})
    return jsonify({"success": False, "message": "Deletion failed"}), 500


# ── Claim query route ─────────────────────────────────────────────────────────

@app.route("/ask", methods=["POST"])
def ask_question():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    data = request.get_json() or {}
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"success": False, "message": "Question cannot be empty"}), 400

    user_id = session["user_id"]
    username = session["username"]

    # Check if user has uploaded any policy document
    user_docs = get_user_documents(user_id)
    if not user_docs:
        return jsonify({
            "success": False,
            "message": "Please upload your insurance policy PDF before asking questions."
        }), 400

    try:
        # ── Semantic cache: check if this user already asked something similar ──
        q_emb = compute_embedding(question)
        prior_rows = fetch_user_questions_with_answers(user_id)

        cached = None
        for row in prior_rows:
            (qid, _qtext, emb_bytes, ans_text, verdict,
             clause_ref, confidence, src_doc, src_page, aid) = row
            if emb_bytes and ans_text:
                old_emb = __import__("numpy").frombuffer(emb_bytes, dtype=__import__("numpy").float64)
                sim = cosine_sim(q_emb, old_emb)
                if sim >= SIMILARITY_THRESHOLD:
                    cached = {
                        "verdict": verdict,
                        "answer": ans_text,
                        "clause_reference": clause_ref,
                        "source_doc": src_doc,
                        "source_page": src_page,
                        "confidence": float(confidence) if confidence else 0.0,
                        "from_cache": True,
                        "question_id": qid,
                        "answer_id": aid
                    }
                    break

        if cached:
            return jsonify({"success": True, **cached})

        # ── Generate new answer ──────────────────────────────────────────────
        verdict, explanation, clause, source_doc, source_page, confidence = \
            get_insurance_answer(user_id, question)

        qid = save_question(user_id, username, question, q_emb)
        aid = save_answer(
            qid, verdict, explanation, clause,
            source_doc, source_page, confidence, False
        )

        return jsonify({
            "success": True,
            "verdict": verdict,
            "answer": explanation,
            "clause_reference": clause,
            "source_doc": source_doc,
            "source_page": source_page,
            "confidence": confidence,
            "from_cache": False,
            "question_id": qid,
            "answer_id": aid
        })

    except Exception as e:
        print(f"Ask error: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"success": False, "message": str(e)}), 500


# ── History & export ──────────────────────────────────────────────────────────

@app.route("/history")
def history():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    h = get_user_history(session["user_id"])
    return jsonify({"success": True, "history": h})


@app.route("/export")
def export_qa():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    h = get_user_history(session["user_id"])
    if not h:
        return "<h2>No data to export</h2><p><a href='/home'>← Back</a></p>", 404

    rows = [{
        "Question": item["question"],
        "Verdict": item["verdict"],
        "Explanation": item["answer"],
        "Clause Reference": item["clause_reference"],
        "Source Document": item["source_doc"],
        "Page": item["source_page"],
        "Confidence": f"{item['confidence'] * 100:.1f}%"
    } for item in h]

    df = pd.DataFrame(rows)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Claim Queries", index=False)
        ws = writer.sheets["Claim Queries"]
        for idx, col in enumerate(df.columns):
            max_len = max(df[col].astype(str).apply(len).max(), len(col)) + 2
            ws.column_dimensions[chr(65 + idx)].width = min(max_len, 60)
    output.seek(0)

    fname = f"InsuranceClaims_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(output,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=fname)


@app.route("/stats")
def stats():
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    s = get_statistics(session["user_id"])
    return jsonify({"success": True, "stats": s})


if __name__ == "__main__":
    print("Starting InsuranceApp...")
    app.run(debug=True, host="0.0.0.0", port=5000)
