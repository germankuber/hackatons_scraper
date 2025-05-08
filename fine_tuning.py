import os
import json
import torch
from dotenv import load_dotenv
from supabase import create_client, Client
from datasets import load_dataset

from transformers import (
    AutoTokenizer,
    AutoModelForCausalLM,
    BitsAndBytesConfig,
    TrainingArguments,
    Trainer,
)
from peft import LoraConfig, get_peft_model, TaskType

# === 0. Ajustes de modelo ===
MODEL_NAME = "meta-llama/Llama-2-3b-hf"  # Hugging Face Hub
OUTPUT_DIR = "output-llama2"
MAX_LENGTH = 512

# === 1. Supabase setup ===
load_dotenv()
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_TOKEN")  # tu token HF

if not HUGGINGFACE_TOKEN:
    raise ValueError("❌ Debes definir HUGGINGFACE_TOKEN en tu .env (o hacer `huggingface-cli login`).")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

# === 2. Export data from Supabase to JSONL ===
def export_to_jsonl(jsonl_path="data/train.jsonl"):
    os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
    print("🔎 Fetching hackathons from Supabase...")

    hackathons = (
        supabase.table("hackathons")
        .select("id,name,description")
        .execute()
        .data
    ) or []

    with open(jsonl_path, "w", encoding="utf-8") as fout:
        for h in hackathons:
            hid = h["id"]
            name = h.get("name", "")
            desc = h.get("description", "")

            print(f"📦 Fetching projects for hackathon '{name}' (id: {hid})...")
            projects = (
                supabase.table("projects")
                .select("title,description,tags")
                .eq("hackathon_id", hid)
                .execute()
                .data
            ) or []

            prompt = f"Hackathon: {name}\nDescription: {desc}\nProjects:\n"
            for p in projects:
                ptags = ", ".join(p.get("tags", []))
                prompt += f" - {p['title']}: {p['description']} (Tags: {ptags})\n"
            prompt += "\nInstruction: Summarize this hackathon in detail.\nResponse:"

            completion = f"Summary of the hackathon '{name}' with {len(projects)} projects: ..."

            fout.write(
                json.dumps({"prompt": prompt, "completion": completion}, ensure_ascii=False)
                + "\n"
            )

    print("✅ Data exported to", jsonl_path)

# === 3. Tokenización del dataset ===
def prepare_dataset(tokenizer, jsonl_path="data/train.jsonl"):
    ds = load_dataset("json", data_files=jsonl_path, split="train")

    def tokenize(example):
        full_text = example["prompt"] + example["completion"]
        tokenized = tokenizer(
            full_text,
            padding="max_length",
            truncation=True,
            max_length=MAX_LENGTH,
            return_attention_mask=True,
        )

        prompt_ids = tokenizer(
            example["prompt"],
            padding="max_length",
            truncation=True,
            max_length=MAX_LENGTH,
        )["input_ids"]

        labels = tokenized["input_ids"].copy()
        for i, tok in enumerate(prompt_ids):
            if tok != tokenizer.pad_token_id:
                labels[i] = -100
        tokenized["labels"] = labels
        return tokenized

    return ds.map(tokenize, remove_columns=["prompt", "completion"])

# === 4. Entrenamiento ===
def main():
    export_to_jsonl()

    print("📦 Cargando tokenizer y modelo Llama 2 3B en 4-bit...")
    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_NAME,
        use_auth_token=HUGGINGFACE_TOKEN,
    )

    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        bnb_4bit_quant_type="nf4",
    )

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        device_map="auto",
        quantization_config=bnb_config,
        torch_dtype=torch.float16,
        use_auth_token=HUGGINGFACE_TOKEN,
    )

    lora_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=8,
        lora_alpha=32,
        lora_dropout=0.05,
    )
    model = get_peft_model(model, lora_cfg)

    print("🧹 Tokenizando dataset...")
    train_ds = prepare_dataset(tokenizer)

    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=1,
        gradient_accumulation_steps=4,
        num_train_epochs=3,
        learning_rate=2e-4,
        fp16=False,
        logging_steps=20,
        save_total_limit=2,
        gradient_checkpointing=True,
    )

    print("🚀 Iniciando fine-tuning...")
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        tokenizer=tokenizer,
    )
    trainer.train()

    model.save_pretrained(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"\n✅ Fine-tuning completo. Modelo guardado en ./{OUTPUT_DIR}")

if __name__ == "__main__":
    main()
