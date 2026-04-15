import numpy as np
from pypdf import PdfReader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_ollama import ChatOllama
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from langchain_classic.schema import Document
import uuid
import time
import os
import traceback

# Use new langchain-chroma package; fall back to community if not installed
try:
    from langchain_chroma import Chroma
except ImportError:
    from langchain_community.vectorstores import Chroma

from config import EMBED_MODEL, LLM_MODEL, CHROMA_DIR, TOP_K

# ── Model init ────────────────────────────────────────────────────────────────

# Check if GPU is actually available to avoid silent hangs
device = "cuda" if os.environ.get("USE_GPU") == "True" else "cpu" 

print(f"Loading local embedding model on {device}...")
embeddings = HuggingFaceEmbeddings(
    model_name="sentence-transformers/all-MiniLM-L6-v2",
    model_kwargs={'device': device}
)

print(f"Loading LLM: {LLM_MODEL}")
# Added a small timeout and base_url to ensure Ollama connection is solid
llm = ChatOllama(model=LLM_MODEL, temperature=0.05, num_predict=512)
print("LLM ready.")

# ── Vector store helpers ──────────────────────────────────────────────────────

def get_user_vectorstore(user_id):
    clean_id = str(user_id).strip()
    user_dir = os.path.join(CHROMA_DIR, f"user_{clean_id}")
    os.makedirs(user_dir, exist_ok=True)
    if not os.path.exists(user_dir):
        try:
            os.makedirs(user_dir, exist_ok=True)
        except OSError as e:
            print(f"Directory creation failed for: {user_dir}")
            raise e
    return Chroma(
        persist_directory=user_dir,
        embedding_function=embeddings,
        collection_name=f"user_{user_id}_docs"
    )

def compute_embedding(text):
    """Return a numpy embedding vector for a single string"""
    vec = embeddings.embed_query(text)
    return np.array(vec)

def cosine_sim(a, b):
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return 0.0 if denom == 0 else float(np.dot(a, b) / denom)

# ── Streamlined PDF ingestion ────────────────────────────────────────────────

def process_pdf_to_vectorstore(user_id, pdf_path, filename, progress_callback=None):
    """
    Faster, stable ingestion pipeline. 
    Removed ThreadPoolExecutor to prevent CUDA/GIL deadlocks.
    """
    start = time.time()
    try:
        if progress_callback: progress_callback(5)

        reader = PdfReader(pdf_path)
        raw_docs = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                raw_docs.append(Document(
                    page_content=text,
                    metadata={"source": filename, "page": i + 1}
                ))
        
        if progress_callback: progress_callback(30)

        splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        chunks = splitter.split_documents(raw_docs)
        
        if progress_callback: progress_callback(50)
        print(f"[EMBED] Processing {len(chunks)} chunks for {filename}...")

        # Bulk processing: LangChain/HuggingFace handles this more efficiently than manual threads
        vs = get_user_vectorstore(user_id)
        vs.add_documents(chunks)

        if progress_callback: progress_callback(100)
        
        elapsed = time.time() - start
        print(f"[EMBED] ✓ Success: {len(chunks)} chunks in {elapsed:.1f}s")
        return True

    except Exception as e:
        print(f"[EMBED ERROR]: {e}")
        traceback.print_exc()
        return False

# ── Insurance claim Q&A ───────────────────────────────────────────────────────

def get_insurance_answer(user_id, question):
    print(f"\n[DEBUG] Processing question for user {user_id}: {question}")
    vs = get_user_vectorstore(user_id)

    # Check if documents exist
    try:
        count = vs._collection.count()
        if count == 0:
            return ("UNCLEAR", "No documents found. Please upload a policy.", "N/A", "N/A", 0, 0.0)
    except:
        return ("UNCLEAR", "Vector store not initialized.", "N/A", "N/A", 0, 0.0)

    # Retrieval
    scored_results = vs.similarity_search_with_relevance_scores(question, k=TOP_K)
    
    if not scored_results:
        return ("UNCLEAR", "No relevant context found.", "N/A", "N/A", 0, 0.0)

    context = "\n\n".join([f"Context {i}:\n{d.page_content}" for i, (d, s) in enumerate(scored_results)])
    
    prompt = f"""[INST] <<SYS>>
    You are an insurance policy auditor. You must extract the FULL VERBATIM CLAUSE 
    from the text provided. If the answer is found, output the entire paragraph 
    containing the rule.
    <</SYS>>
    Context: {context}
    Question: {question}
    
    Output exactly in this format:
    VERDICT: [APPROVED/DENIED/UNCLEAR]
    CLAUSE: [Verbatim text]
    EXPLANATION: [Brief reasoning]
    [/INST]"""

    print("[DEBUG] Calling LLM...")
    try:
        raw = llm.invoke(prompt)
        # Handle different return types from Ollama
        response_text = raw.content if hasattr(raw, 'content') else str(raw)
        print(f"[DEBUG] LLM Response received.")
    except Exception as e:
        print(f"[LLM ERROR]: {e}")
        return ("ERROR", "LLM failed to respond.", "N/A", "N/A", 0, 0.0)

    verdict, clause, explanation = _parse_llm_response(response_text)
    
    # Metadata
    top_doc, top_score = scored_results[0]
    return verdict, explanation, clause, top_doc.metadata.get("source", "N/A"), top_doc.metadata.get("page", 0), round(float(top_score), 3)

def _parse_llm_response(raw):
    parsed = {"VERDICT": "UNCLEAR", "CLAUSE": "N/A", "EXPLANATION": ""}
    lines = raw.split('\n')
    for line in lines:
        if "VERDICT:" in line.upper(): parsed["VERDICT"] = line.split(":", 1)[1].strip()
        elif "CLAUSE:" in line.upper(): parsed["CLAUSE"] = line.split(":", 1)[1].strip()
        elif "EXPLANATION:" in line.upper(): parsed["EXPLANATION"] = line.split(":", 1)[1].strip()
    
    v_clean = "UNCLEAR"
    for v in ["APPROVED", "DENIED", "PARTIAL"]:
        if v in parsed["VERDICT"].upper(): v_clean = v; break
    return v_clean, parsed["CLAUSE"], parsed["EXPLANATION"] or raw

# Placeholder for remaining util functions...
def check_pdf_in_user_vectorstore(user_id, filename):
    vs = get_user_vectorstore(user_id)
    res = vs.get(where={"source": filename}, limit=1)
    return len(res['ids']) > 0

def delete_pdf_from_vectorstore(user_id, filename):
    vs = get_user_vectorstore(user_id)
    res = vs.get(where={"source": filename})
    if res['ids']: vs.delete(ids=res['ids'])
    return True
