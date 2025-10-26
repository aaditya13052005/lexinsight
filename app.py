# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import fitz  # PyMuPDF
import io
import uuid
import requests
import os
import feedparser
from datetime import datetime

# --- project helpers ---
from supabase_client import (
    get_user_by_email,
    create_user,
    get_cases_by_user,
    create_case,
    upload_file,
    save_file_record,
    get_files_by_case,
    download_file_bytes
)
from semantic_processor import semantic_bp, process_and_store_pdf, get_embedding
from hf_config import HF_TOKEN, HF_EMBED_URL, HF_SUMMARY_URL
import cohere
from hf_config import COHERE_API_KEY


# ------------------------
# Flask setup
# ------------------------
app = Flask(__name__)
app.secret_key = "supersecret_change_this_to_something_random"
app.register_blueprint(semantic_bp)

# ------------------------
# Hugging Face API setup (for embeddings + summarization)
# ------------------------

headers = {"Authorization": f"Bearer {HF_TOKEN}"}
co = cohere.Client(COHERE_API_KEY)



    
@app.route("/files_by_case/<case_id>", methods=["GET"])
def files_by_case(case_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    files = get_files_by_case(case_id)
    files_list = [{"file_name": f["file_name"], "file_url": f["file_url"], "id": f["id"]} for f in files]
    return jsonify({"files": files_list})



# ------------------------
# PDF extraction and chunking helpers
# ------------------------
def extract_text_from_pdf_bytes(file_bytes: bytes):
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        text_parts = [page.get_text("text") for page in doc]
        return "\n".join([t.strip() for t in text_parts if t.strip()])
    except Exception as e:
        print("[ERROR] Failed to extract text from PDF:", str(e))
        raise

def chunk_text(text, max_chars=1500):
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]

# ------------------------
# Routes
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
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Invalid email or password")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

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

        # Upload file to Supabase storage
        file_url = upload_file(session['user_id'], case_id, file_bytes, file_name)
        save_file_record(case_id, file_name, file_url, file_id)

        # Process PDF and store embeddings
        process_and_store_pdf(case_id, file_id, file_bytes)

        return jsonify({
            "message": "File uploaded and embedded successfully",
            "filename": file_name,
            "file_url": file_url,
            "file_id": file_id
        })

    except Exception as e:
        print("[ERROR] Upload failed:", str(e))
        return jsonify({"error": str(e)}), 500

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
        query_emb = get_embedding(query)
        from supabase_client import semantic_search
        results = semantic_search(query_emb, top_k=5)

        hits = []
        for r in results:
            hits.append({
                "filename": r.get("file_name"),
                "file_url": r.get("file_url"),
                "text_snippet": (r.get("text") or "")[:500]
            })

        return jsonify({"hits": hits})

    except Exception as e:
        print("[ERROR] Semantic search failed:", e)
        return jsonify({"hits": [], "error": str(e)})

# ------------------------
# FIXED SUMMARIZE PDF ROUTE
# ------------------------

# ------------------------
# Cohere Summarization Helper
# ------------------------

def summarize_text_cohere(text: str, chunk_size=3000, max_tokens=300, temperature=0.3) -> str:
    """
    Summarizes long text using Cohere Chat API.

    Args:
        text (str): Input text to summarize
        chunk_size (int): Max characters per chunk to avoid API limits
        max_tokens (int): Max tokens per summary chunk
        temperature (float): Creativity level

    Returns:
        str: Combined summary
    """
    try:
        text = text.strip()
        if not text:
            return "No text to summarize."

        # Split text into manageable chunks
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]
        summaries = []

        for i, chunk in enumerate(chunks, 1):
            print(f"[INFO] Summarizing chunk {i}/{len(chunks)}...")  # Optional logging
            response = co.chat(
                model="xlarge",
                messages=[
                    {"role": "system", "content": "You are a helpful legal assistant."},
                    {"role": "user", "content": f"Summarize the following text in concise and clear legal points:\n{chunk}"}
                ],
                max_tokens=max_tokens,
                temperature=temperature
            )
            summaries.append(response.generations[0].text.strip())

        # Combine all summaries into a final summary
        final_summary = "\n".join(summaries)
        return final_summary

    except Exception as e:
        print("[ERROR] Cohere summarization failed:", e)
        return "Error generating summary."
@app.route("/summarize_pdf/<case_id>", methods=["POST"])
def summarize_pdf(case_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    file = request.files.get("pdf")
    if not file:
        return jsonify({"error": "No PDF provided"}), 400

    file_bytes = file.read()
    text = extract_text_from_pdf_bytes(file_bytes)

    if not text.strip():
        return jsonify({"error": "No text found in PDF"}), 400

    try:
        # --- Chunk text to avoid size limits ---
        chunk_size = 3000  # characters per chunk
        chunks = [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

        summaries = []

        for idx, chunk in enumerate(chunks, start=1):
            print(f"[INFO] Summarizing chunk {idx}/{len(chunks)}...")
            
            # ✅ Correct Cohere Chat API usage
            response = co.chat(
                model="xlarge",  # use the correct chat model
                messages=[
                    {"role": "system", "content": "You are a helpful legal assistant."},
                    {"role": "user", "content": f"Summarize the following text in concise and clear legal points:\n{chunk}"}
                ],
                max_tokens=300,
                temperature=0.3
            )

            # ✅ Access text correctly
            summaries.append(response.generations[0].text.strip())

        # Combine all chunk summaries
        final_summary = "\n".join(summaries)

        return jsonify({"summary": final_summary})

    except Exception as e:
        print("[ERROR] Cohere summarization failed:", e)
        return jsonify({"error": "Error generating summary."}), 500

# ------------------------
# PUBLIC API ENDPOINTS FOR IBM ORCHESTRATE
# ------------------------

from flask import request

# 1️⃣ Summarizer Agent
@app.route("/api/summarize", methods=["POST"])
def api_summarize():
    """
    Accepts a PDF file and returns its summarized text using Cohere.
    """
    file = request.files.get("pdf")
    if not file:
        return jsonify({"error": "No PDF provided"}), 400

    try:
        file_bytes = file.read()
        text = extract_text_from_pdf_bytes(file_bytes)
        if not text.strip():
            return jsonify({"error": "No readable text found in PDF"}), 400

        # Use your existing summarizer helper
        summary = summarize_text_cohere(text)
        return jsonify({
            "status": "success",
            "summary": summary
        })

    except Exception as e:
        print("[ERROR] /api/summarize failed:", e)
        return jsonify({"error": str(e)}), 500


# 2️⃣ Semantic Search Agent
@app.route("/api/semantic_search", methods=["POST"])
def api_semantic_search():
    """
    Accepts text query -> returns top relevant document snippets.
    """
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Missing 'query'"}), 400

    try:
        query_emb = get_embedding(query)
        from supabase_client import semantic_search
        results = semantic_search(query_emb, top_k=5)

        hits = [{
            "filename": r.get("file_name"),
            "file_url": r.get("file_url"),
            "text_snippet": (r.get("text") or "")[:500]
        } for r in results]

        return jsonify({"status": "success", "results": hits})

    except Exception as e:
        print("[ERROR] /api/semantic_search failed:", e)
        return jsonify({"error": str(e)}), 500


# 3️⃣ Research Scouting Agent



def fetch_articles(topic, max_results=5):
    """
    Fetch legal articles/news from Google News RSS (India, legal context) based on a topic.
    """
    query = topic.replace(" ", "+")
    rss_url = f"https://news.google.com/rss/search?q={query}+legal&hl=en-IN&gl=IN&ceid=IN:en"
    feed = feedparser.parse(rss_url)
    results = []

    for entry in feed.entries[:max_results]:
        published = getattr(entry, "published", "")
        try:
            published_date = datetime.strptime(published, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y-%m-%d")
        except Exception:
            published_date = published

        results.append({
            "title": entry.title,
            "link": entry.link,
            "summary": getattr(entry, "summary", ""),
            "authors": getattr(entry, "author", "Unknown"),
            "published": published_date
        })

    return results

@app.route("/api/scout", methods=["POST"])
def api_scout():
    """
    Free Research Scouting: returns legal news/articles from Google News RSS.
    Works for both web app and AI agent.
    """
    data = request.get_json(force=True)
    topic = data.get("topic") or data.get("query")  # handle both frontend and AI agent keys
    if not topic or not topic.strip():
        return jsonify({"error": "Missing topic"}), 400

    try:
        articles = fetch_articles(topic.strip(), max_results=5)
        return jsonify({"status": "success", "scouting_results": articles})
    except Exception as e:
        print("[ERROR] /api/scout failed:", e)
        return jsonify({"error": str(e)}), 500


# 4️⃣ Case Manager Agent
@app.route("/api/cases", methods=["GET"])
def api_cases():
    """
    Returns a list of cases:
    - Frontend (logged-in users): returns their cases.
    - Orchestrate/public: returns all cases.
    """
    try:
        if 'user_id' in session:
            # Frontend: only user's cases
            user_id = session.get("user_id")
            cases = get_cases_by_user(user_id)
        else:
            # Orchestrate: all cases
            cases = get_all_cases()

        case_list = [
            {"id": c["id"], "title": c["title"], "created_at": c["created_at"]}
            for c in cases
        ]

        return jsonify({"status": "success", "cases": case_list})

    except Exception as e:
        print("[ERROR] /api/cases failed:", e)
        return jsonify({"error": str(e)}), 500


# ------------------------
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, port=5001)
