# interactive.py  (overwrite with this)

import os, torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftConfig, get_peft_model

MODEL_PATH = "deepseek-ai/deepseek-llm-7b-base"
LORA_DIR   = "lora-hackathons"

device = "mps" if torch.backends.mps.is_available() else "cpu"
dtype  = torch.float32

print(f"🔄 Loading full base model on {device}…")
tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH)
base = AutoModelForCausalLM.from_pretrained(
    MODEL_PATH,
    torch_dtype=dtype,
    device_map={ "": device },
    offload_folder=None,
)

print("🔄 Applying LoRA adapter…")
peft_config = PeftConfig.from_pretrained(LORA_DIR)
model = get_peft_model(base, peft_config)

adapter_path = os.path.join(LORA_DIR, "adapter_model.bin")
state = torch.load(adapter_path, map_location=device)
model.load_state_dict(state, strict=False)

model.to(device)
model.eval()

def generate(prompt: str, max_new_tokens: int = 100) -> str:
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            eos_token_id=tokenizer.eos_token_id,
        )
    return tokenizer.decode(out[0], skip_special_tokens=True)

print("\n🧠 Ready on MPS! Type 'exit' to quit.\n")
while True:
    q = input("❓ ").strip()
    if q.lower()=="exit":
        break
    print("\n🤖", generate(q + "\nResponse:"), "\n" + "-"*60 + "\n")
