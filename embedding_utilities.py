from sentence_transformers import SentenceTransformer

# Load model once (lightweight and fast)
model = SentenceTransformer("all-MiniLM-L6-v2")

def get_embeddings(texts):
    """
    Convert one or multiple texts into vector embeddings.
    Input: list of strings
    Output: list of embeddings (list of lists of floats)
    """
    if isinstance(texts, str):
        texts = [texts]
    embeddings = model.encode(texts, show_progress_bar=False).tolist()
    return embeddings
