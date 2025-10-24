import numpy as np
import spacy
import fitz  # PyMuPDF
import re
import ast  # for safely parsing string representations of embeddings
from flask import Blueprint, request, jsonify
from supabase_client import insert_document_chunk, supabase

# -------------------------------
# Blueprint
# -------------------------------
semantic_bp = Blueprint("semantic_bp", __name__)

# -------------------------------
# Load spaCy model once
# -------------------------------
print("[DEBUG] Loading spaCy model...")
nlp = spacy.load("en_core_web_md")
print("[DEBUG] spaCy model loaded successfully.")

# -------------------------------
# Utility: cosine similarity
# -------------------------------
def cosine_similarity(vec1, vec2):
    """Compute cosine similarity between two vectors."""
    v1 = np.array(vec1)
    v2 = np.array(vec2)
    if np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
        return 0.0
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

# -------------------------------
# PDF TEXT EXTRACTION & CHUNKING
# -------------------------------
def extract_text_from_pdf(pdf_bytes):
    """Extract raw text from PDF bytes."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return doc

def split_text_into_chunks(text, max_length=1000):
    """Split text into chunks suitable for embedding."""
    text = re.sub(r'\s+', ' ', text)
    chunks = [text[i:i+max_length] for i in range(0, len(text), max_length)]
    return chunks

# -------------------------------
# PROCESS PDF AND STORE EMBEDDINGS
# -------------------------------
def process_and_store_pdf(case_id: str, file_id: str, file_bytes: bytes):
    """
    Extracts text from PDF bytes, splits into chunks, embeds & stores them
    with page number in Supabase 'documents' table.
    """
    pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
    print(f"[DEBUG] Processing PDF with {len(pdf_doc)} pages...")

    for page_number, page in enumerate(pdf_doc, start=1):
        page_text = page.get_text("text") or ""
        chunks = split_text_into_chunks(page_text, max_length=1000)

        for chunk in chunks:
            emb = nlp(chunk).vector.tolist()
            insert_document_chunk(
                case_id=case_id,
                file_id=file_id,
                text=chunk,
                embedding=emb,
                page_number=page_number
            )

    print(f"[DEBUG] All chunks embedded and stored for case_id={case_id}, file_id={file_id}")

# -------------------------------
# ROUTE: SEMANTIC SEARCH
# -------------------------------
@semantic_bp.route("/semantic_search", methods=["POST"])
def semantic_search_route():
    """
    POST JSON:
    {
        "query": "your search query",
        "case_id": "optional-case-id",
        "top_k": 5
    }
    """
    try:
        data = request.json
        query = data.get("query")
        case_id = data.get("case_id")
        top_k = data.get("top_k", 5)

        if not query:
            return jsonify({"error": "query is required"}), 400

        print(f"[DEBUG] Performing semantic search for query: '{query}'")
        query_vector = nlp(query).vector.tolist()

        # Fetch document chunks
        if case_id:
            response = supabase.table("documents").select("*").eq("case_id", case_id).execute()
        else:
            response = supabase.table("documents").select("*").execute()

        rows = response.data or []
        print(f"[DEBUG] Retrieved {len(rows)} document chunks from Supabase.")

        if not rows:
            return jsonify({"results": []})

        results = []
        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data:
                continue

            # Convert embedding string (if stored as text) to list of floats
            if isinstance(emb_data, str):
                try:
                    emb_data = np.array(ast.literal_eval(emb_data))
                except Exception as e:
                    print(f"[ERROR] Failed to parse embedding for row {row.get('id', 'unknown')}: {e}")
                    continue
            else:
                emb_data = np.array(emb_data)

            # Compute cosine similarity
            try:
                sim = cosine_similarity(query_vector, emb_data)
            except Exception as e:
                print(f"[ERROR] Similarity computation failed: {e}")
                continue

            results.append({
                "chunk": row.get("text"),
                "similarity": sim,
                "file_id": row.get("file_id"),
                "case_id": row.get("case_id"),
                "page_number": row.get("page_number")
            })

        # Sort and return top results
        top_results = sorted(results, key=lambda x: x["similarity"], reverse=True)[:top_k]
        print(f"[DEBUG] Returning top {len(top_results)} results.")
        return jsonify({"results": top_results})

    except Exception as e:
        print(f"[FATAL] Semantic search failed: {e}")
        return jsonify({"error": "Internal server error"}), 500
