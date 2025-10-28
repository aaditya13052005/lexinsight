# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import fitz  # PyMuPDF
import io, uuid, requests, os, xml.etree.ElementTree as ET
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
    download_file_bytes,
    get_all_cases
)
from semantic_processor import semantic_bp, process_and_store_pdf, get_embedding
from cohere import Client
from config import COHERE_API_KEY  # only the API key now

# --- Flask setup ---
app = Flask(__name__)
app.secret_key = "supersecret_change_this_to_something_random"  # stays here
app.register_blueprint(semantic_bp)

# --- Cohere client ---
co = Client(api_key=COHERE_API_KEY)




app.config['DEBUG'] = True
app.config['PROPAGATE_EXCEPTIONS'] = True


# -------------------------------------------------------------------
    
@app.route("/files_by_case/<case_id>", methods=["GET"])
def files_by_case(case_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    files = get_files_by_case(case_id)
    files_list = [{"file_name": f["file_name"], "file_url": f["file_url"], "id": f["id"]} for f in files]
    return jsonify({"files": files_list})

# --------------------------------------------------------------
def query_hf_inlegalbert(prompt: str):
    """
    Query the InLegalBERT model via Hugging Face API.
    Works without local model hosting.
    """
    try:
        payload = {"inputs": prompt}
        response = requests.post(HF_API_URL, headers=HEADERS, json=payload, timeout=15)
        response.raise_for_status()
        result = response.json()

        # Handle standard Hugging Face inference formats
        if isinstance(result, list) and len(result) > 0:
            return result[0].get("generated_text") or result[0].get("sequence") or "No result."
        elif isinstance(result, dict):
            return result.get("generated_text", "No output.")
        else:
            return "No response from InLegalBERT."
    except Exception as e:
        print("[ERROR] InLegalBERT request failed:", e)
        return f"Error querying InLegalBERT: {str(e)}"




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
    Fetch legal articles/news from Google News RSS using only built-in libraries.
    """
    query = topic.replace(" ", "+")
    rss_url = f"https://news.google.com/rss/search?q={query}+legal&hl=en-IN&gl=IN&ceid=IN:en"

    try:
        resp = requests.get(rss_url, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print("[ERROR] Failed to fetch RSS:", e)
        return []

    root = ET.fromstring(resp.content)
    items = root.findall(".//item")[:max_results]
    results = []

    for item in items:
        title = item.findtext("title", default="No title")
        link = item.findtext("link", default="#")
        summary = item.findtext("description", default="")
        pub_date = item.findtext("pubDate", default="Unknown")
        try:
            published_date = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %Z").strftime("%Y-%m-%d")
        except Exception:
            published_date = pub_date

        results.append({
            "title": title,
            "link": link,
            "summary": summary,
            "authors": "Unknown",
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
