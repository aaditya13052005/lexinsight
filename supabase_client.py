from supabase import create_client, Client
import uuid
import requests
import io

SUPABASE_URL = "https://vxlmdnjgpladqylpacwl.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ4bG1kbmpncGxhZHF5bHBhY3dsIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTkyMDkzMDUsImV4cCI6MjA3NDc4NTMwNX0.KLY4COlqMswqrp5GMYbSydScM87gDR5rktZQ7CLjWJk"
SUPABASE_SERVICE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZ4bG1kbmpncGxhZHF5bHBhY3dsIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTIwOTMwNSwiZXhwIjoyMDc0Nzg1MzA1fQ.EMWvFsrw-iqwx2kQq5g4x13Q9wU6n_8xsGkNHtSExyQ"

# Initialize supabase client
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ------------------------
# USER FUNCTIONS
# ------------------------
def get_user_by_email(email: str):
    response = supabase.table("users").select("*").eq("email", email).execute()
    if response.data and len(response.data) > 0:
        return response.data[0]
    return None

def create_user(name: str, email: str, password: str):
    data = {"name": name, "email": email, "password": password}
    response = supabase.table("users").insert(data).execute()
    return response.data[0] if response.data else None

# ------------------------
# CASE FUNCTIONS
# ------------------------
def get_cases_by_user(user_id: str):
    response = supabase.table("cases").select("*").eq("user_id", user_id).execute()
    return response.data or []

def create_case(user_id: str, title: str):
    data = {"user_id": user_id, "title": title}
    response = supabase.table("cases").insert(data).execute()
    return response.data[0] if response.data else None

# ------------------------
# FILE FUNCTIONS
# ------------------------
def upload_file(user_id: str, case_id: str, file_bytes: bytes, file_name: str) -> str:
    """
    Uploads a file to Supabase storage and returns its public URL.
    """
    bucket_path = f"{user_id}/{case_id}/{file_name}"
    try:
        supabase.storage.from_("case-pdfs").upload(bucket_path, file_bytes)
        public_url = supabase.storage.from_("case-pdfs").get_public_url(bucket_path)
        return public_url
    except Exception as e:
        print(f"[ERROR] Supabase upload failed: {e}")
        raise e

def save_file_record(case_id: str, file_name: str, file_url: str, file_id: str):
    try:
        supabase.table("files").insert({
            "id": file_id,
            "case_id": case_id,
            "file_name": file_name,
            "file_url": file_url
        }).execute()
        print(f"[DEBUG] File record saved: {file_name}, file_id={file_id}")
    except Exception as e:
        print(f"[ERROR] Failed to save file record: {e}")
        raise e

def get_files_by_case(case_id: str):
    response = supabase.table("files").select("*").eq("case_id", case_id).execute()
    return response.data or []

def download_file_bytes(file_url: str):
    r = requests.get(file_url)
    if r.status_code == 200:
        return io.BytesIO(r.content).read()
    else:
        raise Exception(f"Failed to download file: {r.status_code}")

# ------------------------
# SEMANTIC SEARCH FUNCTIONS
# ------------------------
def insert_document_chunk(case_id: str, file_id: str, text: str, embedding: list, page_number: int = None):
    data = {
        "case_id": case_id,
        "file_id": file_id,
        "text": text,
        "embedding": embedding
    }
    if page_number is not None:
        data["page_number"] = page_number

    try:
        supabase.table("documents").insert(data).execute()
        print(f"[DEBUG] Inserted chunk for file {file_id}, page {page_number}")
    except Exception as e:
        print(f"[ERROR] Failed to insert document chunk: {e}")
        raise e

def semantic_search(query_embedding: list, top_k: int = 5):
    try:
        response = supabase.rpc(
            "match_documents",
            {"query_embedding": query_embedding, "match_count": top_k}
        ).execute()
        return response.data
    except Exception as e:
        print(f"[ERROR] Semantic search failed: {e}")
        raise e
