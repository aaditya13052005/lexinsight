# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import fitz  # PyMuPDF
import io
import uuid
import spacy
import requests

# --- project helpers ---
from supabase_client import (
    get_user_by_email,
    create_user,
    get_cases_by_user,
    create_case,
    upload_file,
    save_file_record,
    get_files_by_case,
    download_file_bytes,
    insert_document_chunk,
    semantic_search
)
from semantic_processor import semantic_bp, process_and_store_pdf

# ------------------------
# Flask setup
# ------------------------
app = Flask(__name__)

# 🔑 Set secret key BEFORE any session usage or blueprint registration
app.secret_key = "supersecret_change_this_to_something_random"

# register blueprint only once
app.register_blueprint(semantic_bp)

CROSSREF_API_URL = "https://api.crossref.org/works" 

# ------------------------
# spaCy embedding model (load once)
# ------------------------
print("[DEBUG] Loading spaCy model...")
nlp = spacy.load("en_core_web_sm")

print("[DEBUG] spaCy model loaded successfully.")

def get_embedding_spacy(text: str):
    doc = nlp(text)
    return doc.vector.tolist()

# ----------------------------
# New Scouting Route
# ----------------------------
import os
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # optional to hide warnings

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@app.route("/scout", methods=["POST"])
def scout():
    """
    Accepts JSON: {"query": "contract breaches employment law"}
    Returns top 5 research paper metadata from CrossRef
    """
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "Query cannot be empty"}), 400

    try:
        params = {
            "query.bibliographic": query,
            "rows": 5,
            "sort": "relevance"
        }

        # Increased timeout and SSL verification disabled
        response = requests.get(CROSSREF_API_URL, params=params, timeout=30, verify=False)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])

        results = []
        for item in items:
            results.append({
                "title": item.get("title", ["No title"])[0],
                "authors": ", ".join([f"{a.get('given','')} {a.get('family','')}" for a in item.get("author", [])]) if "author" in item else "Unknown",
                "published": "-".join(map(str, item.get("published-print", {}).get("date-parts", [[None]])[0])) if "published-print" in item else "N/A",
                "link": item.get("URL", "")
            })

        return jsonify(results)

    except requests.exceptions.Timeout:
        print("[ERROR] CrossRef request timed out")
        return jsonify({"error": "Request timed out"}), 504
    except Exception as e:
        print("[ERROR] CrossRef query failed:", e)
        return jsonify({"error": "Could not fetch research papers"}), 500
    

# ------------------------
# PDF Summarization Route (Hugging Face hosted open-source)
# ------------------------
# ------------------------
# PDF Summarization Route (Hugging Face hosted open-source)
# ------------------------
HF_API_KEY = "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"
HF_MODEL = "facebook/bart-large-cnn"
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"
HEADERS = {"Authorization": f"Bearer {HF_API_KEY}"}


def extract_text_from_pdf_bytes(file_bytes: bytes):
    """Extract plain text from PDF bytes using PyMuPDF"""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_parts = [page.get_text("text") for page in doc]
        return "\n".join([t.strip() for t in text_parts if t.strip()])
    except Exception as e:
        print("[ERROR] Failed to extract text from PDF:", str(e))
        raise


def chunk_text(text, max_chars=1500):
    """Split text into small pieces for summarization"""
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def summarize_chunk(chunk):
    """Send text chunk to Hugging Face for summarization"""
    payload = {
        "inputs": chunk,
        "parameters": {"max_length": 200, "min_length": 50, "do_sample": False}
    }
    response = requests.post(HF_API_URL, headers=HEADERS, json=payload)
    response.raise_for_status()
    result = response.json()
    if isinstance(result, list) and "summary_text" in result[0]:
        return result[0]["summary_text"]
    return ""


@app.route("/summarize_pdf/<case_id>", methods=["POST"])
def summarize_pdf(case_id):
    """Summarize a PDF using Hugging Face Pegasus model"""
    try:
        file = request.files.get("pdf")
        if not file:
            return jsonify({"error": "No PDF uploaded"}), 400

        pdf_bytes = file.read()
        text = extract_text_from_pdf_bytes(pdf_bytes)

        if not text.strip():
            return jsonify({"error": "No readable text found in PDF"}), 400

        chunks = chunk_text(text)
        summaries = []
        for chunk in chunks:
            try:
                summary = summarize_chunk(chunk)
                if summary:
                    summaries.append(summary)
            except Exception as e:
                print(f"[WARN] Chunk summarization failed: {e}")

        final_summary = " ".join(summaries).strip()
        if not final_summary:
            final_summary = "No meaningful summary generated."

        return jsonify({"summary": final_summary})

    except requests.exceptions.RequestException as e:
        print("[ERROR] HuggingFace API Error:", e)
        return jsonify({"error": f"HuggingFace API error: {str(e)}"}), 500

    except Exception as e:
        print("[ERROR] Summarization failed:", e)
        return jsonify({"error": str(e)}), 500

# -----------------------
# Helper function to extract text from PDF bytes
# ------------------------
# def extract_text_from_pdf_bytes(file_bytes: bytes):
#     try:
#         doc = fitz.open(stream=file_bytes, filetype="pdf")
#         text_parts = [page.get_text("text") for page in doc]
#         return "\n".join(text_parts)
#     except Exception as e:
#         print("[ERROR] Failed to extract text from bytes:", str(e))
#         raise

# ------------------------
# AUTH ROUTES
# ------------------------
@app.route('/')
def home():
    return redirect(url_for('login'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        name = request.form.get('name', '')

        if get_user_by_email(email):
            return render_template('register.html', error="Email already registered")

        hashed_password = generate_password_hash(password)
        create_user(name, email, hashed_password)
        print(f"[DEBUG] Registered new user: {email}")
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        user = get_user_by_email(email)
        if user and check_password_hash(user['password'], password):
            session['user_id'] = user['id']
            session['user_name'] = user.get('name', '')
            print(f"[DEBUG] User logged in: {email}")
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Invalid email or password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ------------------------
# DASHBOARD & CASE ROUTES
# ------------------------
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    cases = get_cases_by_user(session['user_id'])
    return render_template('dashboard.html', cases=cases)

@app.route('/create_case', methods=['POST'])
def create_case_route():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    title = request.form['title']
    new_case = create_case(session['user_id'], title)
    case_id = new_case['id'] if new_case else None
    return redirect(url_for('case_view', case_id=case_id))

@app.route('/case/<case_id>', methods=['GET'])
def case_view(case_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    documents = get_files_by_case(case_id)
    docs_list = [{"file_name": doc.get("file_name"), "file_url": doc.get("file_url")} for doc in documents]
    return render_template("case.html", case_id=case_id, files=docs_list)

# ------------------------
# UPLOAD PDF
# ------------------------
@app.route('/upload_file/<case_id>', methods=['POST'])
def upload_pdf(case_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get('pdf')
    if not file:
        return jsonify({"error": "No file provided"}), 400

    try:
        file_id = str(uuid.uuid4())
        file_bytes = file.read()
        file_name = file.filename

        # 1️⃣ Upload file to Supabase storage
        file_url = upload_file(session['user_id'], case_id, file_bytes, file_name)
        print(f"[DEBUG] Uploaded file: {file_url}")

        # 2️⃣ Save file record in DB
        save_file_record(case_id, file_name, file_url, file_id)
        print(f"[DEBUG] Saved DB record: {file_name}")

        # 3️⃣ Process PDF and store embeddings in Supabase
        process_and_store_pdf(case_id, file_id, file_bytes)
        print(f"[DEBUG] Stored semantic embeddings for case_id={case_id}")

        return jsonify({
            "message": "File uploaded and embedded successfully",
            "filename": file_name,
            "file_url": file_url,
            "file_id": file_id
        })

    except Exception as e:
        print("[ERROR] Upload failed:", str(e))
        return jsonify({"error": str(e)}), 500

# ------------------------
# FILES LIST
# ------------------------
@app.route('/files_by_case/<case_id>', methods=['GET'])
def files_by_case_route(case_id):
    try:
        files = get_files_by_case(case_id)
        files_list = [{"file_name": f.get("file_name"), "file_url": f.get("file_url")} for f in files]
        return jsonify({"files": files_list})
    except Exception as e:
        return jsonify({"error": str(e), "files": []}), 500

# ------------------------
# SEMANTIC SEARCH FOR PDFs
# ------------------------
@app.route("/search_pdf", methods=["POST"])
def search_pdf():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    case_id = data.get("case_id")
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"hits": []})

    try:
        # Generate embedding for the query
        query_emb = nlp(query).vector.tolist()

        # Perform semantic search
        results = semantic_search(query_emb, top_k=5)

        # Format results for frontend
        hits = []
        for r in results:
            hits.append({
                "filename": r.get("file_name"),
                "file_url": r.get("file_url"),
                "text_snippet": (r.get("text") or "")[:500]  # show first 500 chars
            })

        return jsonify({"hits": hits})

    except Exception as e:
        print("[ERROR] Semantic search failed:", e)
        return jsonify({"hits": [], "error": str(e)})

# ------------------------
# SERVE PDF PAGE AS IMAGE
# ------------------------
@app.route("/pdf_image/<case_id>/<filename>/<int:page_num>")
def pdf_page_image(case_id, filename, page_num):
    files = get_files_by_case(case_id)
    file_obj = next((f for f in files if f['file_name'] == filename), None)
    if not file_obj:
        return "File not found", 404

    try:
        file_bytes = download_file_bytes(file_obj['file_url'])
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if page_num < 1 or page_num > len(doc):
            return "Invalid page", 400

        page = doc[page_num - 1]
        pix = page.get_pixmap()
        img_bytes = pix.tobytes("png")
        return send_file(io.BytesIO(img_bytes), mimetype="image/png")
    except Exception as e:
        return "Failed to render page", 500

# ------------------------
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5001)
