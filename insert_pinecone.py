# insert_pinecone.py
import os
from typing import Dict, List

import cohere
from dotenv import load_dotenv

load_dotenv()

from openai import OpenAI
from pinecone import Pinecone, ServerlessSpec
from pinecone.openapi_support.exceptions import PineconeApiException
from supabase import Client, create_client

# ─── LOAD ENVIRONMENT VARIABLES ─────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_CLOUD = os.getenv("PINECONE_CLOUD", "aws")  # one of: "gcp", "aws", "azure"
PINECONE_REGION = os.getenv("PINECONE_REGION", "us-east-1")
PINECONE_INDEX = os.getenv("PINECONE_INDEX_NAME", "projects-hackathon")
COHERE_API_KEY = os.getenv("COHERE_API_KEY", "")

# ─── INITIALIZE CLIENTS ─────────────────────────────────────────────────────
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)
pc = Pinecone(api_key=PINECONE_API_KEY)
co = cohere.Client(COHERE_API_KEY)


# ─── ENSURE PINECONE INDEX EXISTS ────────────────────────────────────────────
try:
    existing = pc.list_indexes().names()
except AttributeError:
    existing = pc.list_indexes()

if PINECONE_INDEX not in existing:
    spec = ServerlessSpec(cloud=PINECONE_CLOUD, region=PINECONE_REGION)
    try:
        pc.create_index(
            name=PINECONE_INDEX,
            dimension=1024,  # adjust to your embedding size
            metric="cosine",  # or "euclidean", etc.
            spec=spec,
        )
        print(f"[✔] Created index '{PINECONE_INDEX}'")
    except PineconeApiException as e:
        if e.status == 409:
            print(f"[ℹ] Index '{PINECONE_INDEX}' already exists (409).")
        else:
            raise
else:
    print(f"[ℹ] Index '{PINECONE_INDEX}' already exists.")

# Reference to the index client
index = pc.Index(PINECONE_INDEX)


# ─── FUNCTIONS ───────────────────────────────────────────────────────────────


def get_projects(limit: int = 100) -> List[Dict]:
    """
    Fetch up to `limit` projects from Supabase.
    Each project must have id, title, description, tags, url fields.
    """
    resp = (
        supabase.table("projects")
        .select("id, title, description, tags, url")
        .limit(limit)
        .execute()
    )
    return resp.data or []


def embed_and_upsert(projects: List[Dict]) -> None:
    """
    Generate embeddings for each project (title + description)
    and upsert them into Pinecone with metadata.
    """
    # Prepare inputs
    texts = [f"{p['title']}\n{p['description']}" for p in projects]
    ids = [str(p["id"]) for p in projects]
    metas = [
        {"title": p["title"], "url": p["url"],"description": p["description"], "tags": p.get("tags", [])}
        for p in projects
    ]

    # Generate embeddings
    # response = client.embeddings.create(input=texts, model="embed-multilingual-v3.0")
    response = co.embed(
        texts=texts,
        model="embed-multilingual-v3.0",
        input_type="search_document",  # o "search_query" o "classification" según el caso
    )

    # Each item in response.data is an object with attribute `.embedding`

    embeddings = response.embeddings

    vectors = list(zip(ids, embeddings, metas))
    index.upsert(vectors=vectors)
    print(f"[✔] Upserted {len(vectors)} vectors into index '{PINECONE_INDEX}'")


# ─── MAIN EXECUTION ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    projects = get_projects(limit=200)
    if not projects:
        print("No projects found to embed.")
    else:
        embed_and_upsert(projects)
