# semantic_processor.py
import numpy as np
import fitz  # PyMuPDF
import re
import ast
from flask import Blueprint, request, jsonify
from supabase_client import insert_document_chunk, supabase
from hf_config import COHERE_API_KEY
import cohere

# -------------------------------
# Flask Blueprint
# -------------------------------
semantic_bp = Blueprint("semantic_bp", __name__)

# -------------------------------
# Cohere client
# -------------------------------
co = cohere.Client(COHERE_API_KEY)

# -------------------------------
# Cohere embedding helper
# -------------------------------
def get_embedding(text: str):
    """Generate embedding using Cohere Embed API for semantic search."""
    try:
        response = co.embed(
            model="embed-english-v3.0",       # or "embed-english-v4.0"
            texts=[text],                     # must be a list of strings
            input_type="search_document"      # mandatory for document embeddings
        )
        return response.embeddings[0]
    except Exception as e:
        print(f"[ERROR] Cohere embedding failed: {e}")
        raise

# -------------------------------
# Cosine similarity
# -------------------------------
def cosine_similarity(vec1, vec2):
    v1, v2 = np.array(vec1), np.array(vec2)
    if np.linalg.norm(v1) == 0 or np.linalg.norm(v2) == 0:
        return 0.0
    return float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

# -------------------------------
# Text chunking
# -------------------------------
def split_text_into_chunks(text, max_length=1000):
    text = re.sub(r'\s+', ' ', text)
    return [text[i:i+max_length] for i in range(0, len(text), max_length)]

# -------------------------------
# Process and store PDF
# -------------------------------
def process_and_store_pdf(case_id: str, file_id: str, file_bytes: bytes):
    pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
    print(f"[DEBUG] Processing PDF with {len(pdf_doc)} pages...")

    for page_number, page in enumerate(pdf_doc, start=1):
        page_text = page.get_text("text") or ""
        chunks = split_text_into_chunks(page_text, max_length=1000)

        for chunk in chunks:
            emb = get_embedding(chunk)
            insert_document_chunk(
                case_id=case_id,
                file_id=file_id,
                text=chunk,
                embedding=emb,
                page_number=page_number
            )

    print(f"[DEBUG] All chunks embedded and stored for case_id={case_id}, file_id={file_id}")

# -------------------------------
# Semantic search endpoint
# -------------------------------
@semantic_bp.route("/semantic_search", methods=["POST"])
def semantic_search_route():
    try:
        data = request.json
        query = data.get("query")
        case_id = data.get("case_id")
        top_k = data.get("top_k", 5)

        if not query:
            return jsonify({"error": "query is required"}), 400

        query_vector = get_embedding(query)

        if case_id:
            response = supabase.table("documents").select("*").eq("case_id", case_id).execute()
        else:
            response = supabase.table("documents").select("*").execute()

        rows = response.data or []
        results = []

        for row in rows:
            emb_data = row.get("embedding")
            if not emb_data:
                continue

            if isinstance(emb_data, str):
                try:
                    emb_data = np.array(ast.literal_eval(emb_data))
                except:
                    continue
            else:
                emb_data = np.array(emb_data)

            sim = cosine_similarity(query_vector, emb_data)
            results.append({
                "chunk": row.get("text"),
                "similarity": sim,
                "file_id": row.get("file_id"),
                "case_id": row.get("case_id"),
                "page_number": row.get("page_number")
            })

        top_results = sorted(results, key=lambda x: x["similarity"], reverse=True)[:top_k]
        return jsonify({"results": top_results})

    except Exception as e:
        print(f"[FATAL] Semantic search failed: {e}")
        return jsonify({"error": "Internal server error"}), 500
