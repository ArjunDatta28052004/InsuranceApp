import numpy as np
from pypdf import PdfReader
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.llms import Ollama
from langchain_classic.text_splitter import RecursiveCharacterTextSplitter
from langchain_classic.schema import Document
import time
import os
import traceback

# Use new langchain-chroma package (run: pip install langchain-chroma)
try:
    from langchain_chroma import Chroma
    print("Using langchain_chroma (new)")
except ImportError:
    from langchain_community.vectorstores import Chroma
    print("Using legacy Chroma")

from config import EMBED_MODEL, LLM_MODEL, CHROMA_DIR, TOP_K

embeddings = OllamaEmbeddings(model=EMBED_MODEL)
llm = Ollama(model=LLM_MODEL, temperature=0.05, num_predict=512)


# ── Per-user vector store helpers ─────────────────────────────────────────────

def get_user_vectorstore(user_id):
    """Load (or create) a per-user Chroma vector store"""
    user_dir = os.path.join(CHROMA_DIR, f"user_{user_id}")
    os.makedirs(user_dir, exist_ok=True)
    return Chroma(
        persist_directory=user_dir,
        embedding_function=embeddings,
        collection_name=f"user_{user_id}_docs"
    )


def cosine_sim(a, b):
    """Cosine similarity between two numpy vectors"""
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


# ── PDF ingestion ─────────────────────────────────────────────────────────────

def process_pdf_to_vectorstore(user_id, pdf_path, filename, progress_callback=None):
    """
    Parse PDF and store chunks in the user's private vector store.
    Each chunk is tagged with the source filename and page number.
    """
    start = time.time()

    if progress_callback:
        progress_callback(5)

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    raw_docs = []

    for page_num, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            raw_docs.append(Document(
                page_content=text,
                metadata={"source": filename, "page": page_num + 1}
            ))
        if progress_callback:
            progress_callback(5 + int((page_num / total_pages) * 35))

    if not raw_docs:
        raise ValueError("No extractable text found in PDF.")

    if progress_callback:
        progress_callback(40)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        length_function=len
    )
    chunks = splitter.split_documents(raw_docs)

    if progress_callback:
        progress_callback(50)

    vs = get_user_vectorstore(user_id)
    batch_size = 50
    total_batches = max(1, (len(chunks) + batch_size - 1) // batch_size)

    for i in range(0, len(chunks), batch_size):
        vs.add_documents(chunks[i: i + batch_size])
        pct = 50 + int(((i // batch_size + 1) / total_batches) * 45)
        if progress_callback:
            progress_callback(min(pct, 95))

    if progress_callback:
        progress_callback(100)

    elapsed = time.time() - start
    print(f"PDF '{filename}' → {len(chunks)} chunks in {elapsed:.1f}s for user {user_id}")


def check_pdf_in_user_vectorstore(user_id, filename):
    """Return True if any chunk from this filename is already in the user's store"""
    try:
        vs = get_user_vectorstore(user_id)
        results = vs.similarity_search("test", k=1, filter={"source": filename})
        return len(results) > 0
    except Exception:
        return False


def delete_pdf_from_vectorstore(user_id, filename):
    """Remove all chunks from a specific PDF from the user's store"""
    try:
        vs = get_user_vectorstore(user_id)
        collection = vs._collection
        # Get all IDs matching the source metadata
        results = collection.get(where={"source": filename})
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
            print(f"Deleted {len(ids)} chunks for '{filename}' from user {user_id}")
        return len(ids)
    except Exception as e:
        print(f"Error deleting from vectorstore: {e}")
        return 0


# ── Insurance-specific Q&A ────────────────────────────────────────────────────

INSURANCE_PROMPT = """You are an expert insurance claims analyst. A user has submitted an insurance claim query.
Your job is to:
1. Carefully read the relevant policy clauses from the context below.
2. Determine if the claim is APPROVED, DENIED, PARTIAL, or UNCLEAR based strictly on the policy text.
3. Quote the exact clause(s) that support your verdict.
4. Give a clear, professional explanation.

IMPORTANT RULES:
- Base your verdict ONLY on the provided context (the actual policy document).
- If the context does not have enough information, say UNCLEAR and explain what is missing.
- Always cite the specific clause, section number, or policy language.
- Be concise but thorough.

Policy Context:
{context}

Claim Query: {question}

Respond in this EXACT format:
VERDICT: [APPROVED / DENIED / PARTIAL / UNCLEAR]
CLAUSE: [Exact quote or section reference from the policy]
EXPLANATION: [Clear professional explanation of why]"""


def get_insurance_answer(user_id, question):
    """
    Search the user's private vector store for relevant policy text,
    then call the LLM with the insurance-specific prompt.

    Returns: (verdict, explanation, clause_reference, source_doc, source_page, confidence)
    """
    vs = get_user_vectorstore(user_id)

    # Check if user has any documents
    try:
        count = vs._collection.count()
    except Exception:
        count = 0

    if count == 0:
        return (
            "UNCLEAR",
            "No insurance documents have been uploaded. Please upload your policy PDF first.",
            "",
            "N/A",
            0,
            0.0
        )

    # Retrieve top-K relevant chunks with similarity scores
    try:
        scored_results = vs.similarity_search_with_score(question, k=TOP_K)
    except Exception:
        docs = vs.similarity_search(question, k=TOP_K)
        scored_results = [(d, 0.5) for d in docs]

    if not scored_results:
        return (
            "UNCLEAR",
            "No relevant policy sections found for this query.",
            "",
            "N/A",
            0,
            0.0
        )

    # Build context from retrieved chunks
    context_parts = []
    for i, (doc, _dist) in enumerate(scored_results, 1):
        page = doc.metadata.get("page", "?")
        src = doc.metadata.get("source", "unknown")
        context_parts.append(f"[Section {i} — {src}, Page {page}]\n{doc.page_content}")
    context = "\n\n".join(context_parts)

    # Compute confidence from distances (ChromaDB returns L2 distance — lower = closer)
    distances = [dist for _, dist in scored_results]
    similarities = [1.0 / (1.0 + d) for d in distances]
    confidence = float(np.mean(similarities))
    confidence = round(min(1.0, max(0.0, confidence)), 3)

    # Call LLM
    prompt = INSURANCE_PROMPT.format(context=context, question=question)
    raw_answer = llm.invoke(prompt)
    if hasattr(raw_answer, "content"):
        raw_answer = raw_answer.content
    raw_answer = str(raw_answer).strip()

    # Parse structured response
    verdict, clause, explanation = _parse_llm_response(raw_answer)

    # Confidence adjustment
    if verdict == "UNCLEAR":
        confidence *= 0.6
    elif verdict in ("APPROVED", "DENIED"):
        confidence = min(1.0, confidence * 1.1)

    # Source attribution from top result
    top_meta = scored_results[0][0].metadata
    source_doc = top_meta.get("source", "N/A")
    source_page = top_meta.get("page", 0)

    return verdict, explanation, clause, source_doc, source_page, confidence


def _parse_llm_response(raw):
    """
    Extract VERDICT, CLAUSE, EXPLANATION from the LLM response.
    Falls back gracefully if the format isn't followed.
    """
    verdict = "UNCLEAR"
    clause = ""
    explanation = raw  # default: return everything as explanation

    lines = raw.splitlines()
    current_field = None
    field_buffer = []

    parsed = {"VERDICT": "", "CLAUSE": "", "EXPLANATION": ""}

    for line in lines:
        stripped = line.strip()
        for field in ("VERDICT", "CLAUSE", "EXPLANATION"):
            if stripped.upper().startswith(f"{field}:"):
                if current_field:
                    parsed[current_field] = " ".join(field_buffer).strip()
                current_field = field
                field_buffer = [stripped[len(field) + 1:].strip()]
                break
        else:
            if current_field:
                field_buffer.append(stripped)

    if current_field:
        parsed[current_field] = " ".join(field_buffer).strip()

    raw_verdict = parsed["VERDICT"].upper()
    for v in ("APPROVED", "DENIED", "PARTIAL", "UNCLEAR"):
        if v in raw_verdict:
            verdict = v
            break

    clause = parsed["CLAUSE"] or ""
    explanation = parsed["EXPLANATION"] or raw

    return verdict, clause, explanation


# ── Semantic cache helper ─────────────────────────────────────────────────────

def compute_embedding(text):
    """Return a numpy array embedding for the given text"""
    return np.array(embeddings.embed_query(text))