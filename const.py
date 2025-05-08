from dotenv import load_dotenv

import os


load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# === 2. Configuration ===
MODEL_PATH = "deepseek-ai/deepseek-llm-7b-base"
OUTPUT_DIR = "lora-hackathons"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")
PINECONE_ENV = os.getenv("PINECONE_ENVIRONMENT")
PINECONE_INDEX = os.getenv("PINECONE_INDEX_NAME", "taikai-projects")

