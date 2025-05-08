# convert_adapter.py

import torch
import safetensors.torch as st
from pathlib import Path

LORA_DIR = Path("lora-hackathons")
sf = LORA_DIR / "adapter_model.safetensors"
bin_out = LORA_DIR / "adapter_model.bin"

# 1) Load the SAFETENSORS adapter
state_dict = st.load_file(str(sf))

# 2) Save it out as a .bin
torch.save(state_dict, str(bin_out))

print(f"✅ Converted {sf.name} → {bin_out.name}")
