# Fine-Tuning a Small Model for LabVIEW-to-Python Conversion

This guide outlines how to fine-tune a small language model (1.5B-3B parameters) to convert LabVIEW VI summaries to Python code, achieving results comparable to larger models at a fraction of the cost and latency.

## Overview

**Goal:** Train a model that takes structured VI summaries and outputs correct Python code.

**Why this works:**
- Narrow domain with consistent input/output patterns
- Fixed input schema (the summary format)
- Limited output vocabulary (Python syntax + specific libraries)
- Deterministic transformations for many primitives

**Expected outcome:** A 1.5B-3B model that runs in <2GB VRAM and produces correct Python in <1 second.

---

## Phase 1: Training Data Collection

### Option A: Generate from Existing VIs (Recommended)

1. **Collect LabVIEW VIs** from open-source projects:
   - JKI VI Package Manager packages
   - OpenG libraries
   - LAVA community contributions
   - NI Example Finder projects

2. **Extract XML using pylabview:**
   ```bash
   # For each .vi file
   python -m pylabview --export-xml input.vi -o output_dir/
   ```

3. **Generate summaries using lvpy:**
   ```bash
   lvpy summarize output_dir/*_BDHb.xml > summaries/
   ```

4. **Generate correct Python using Claude API:**
   ```python
   import anthropic

   client = anthropic.Anthropic()

   def generate_training_pair(summary: str) -> dict:
       response = client.messages.create(
           model="claude-sonnet-4-20250514",
           max_tokens=2000,
           messages=[{
               "role": "user",
               "content": f"""Convert this LabVIEW VI to Python:

   {summary}

   Requirements:
   - Use os.path for path operations
   - Use os.environ for system directories
   - Use os.makedirs with exist_ok=True
   - Output ONLY the Python code, no explanations."""
           }]
       )
       return {
           "input": summary,
           "output": response.content[0].text
       }
   ```

### Option B: Synthetic Data Generation

Generate synthetic VI summaries covering common patterns:

```python
import random
import json

PRIMITIVES = [
    ("Build Path", "os.path.join(base, *names)"),
    ("Strip Path", "os.path.split(path)"),
    ("Concatenate Strings", "str1 + str2"),
    ("Read from Text File", "open(path).read()"),
    ("Write to Text File", "open(path, 'w').write(text)"),
    # ... add more
]

SYSTEM_DIRS = [
    (0, "User Home", "USERPROFILE", "HOME"),
    (3, "User Application Data", "APPDATA", "~/.config"),
    (7, "Public Application Data", "PROGRAMDATA", "/usr/local/share"),
    # ... add more
]

def generate_synthetic_pair():
    """Generate a random VI summary and corresponding Python."""
    # Pick random primitives
    ops = random.sample(PRIMITIVES, k=random.randint(1, 4))

    # Build summary
    summary_lines = ['LabVIEW VI: "Synthetic_VI"', "", "OPERATIONS:"]
    python_lines = ["import os", "", "def synthetic_vi():"]

    for i, (name, python_eq) in enumerate(ops, 1):
        summary_lines.append(f"  [{i}] {name}")
        summary_lines.append(f"       Python: {python_eq}")
        python_lines.append(f"    # {name}")
        python_lines.append(f"    result_{i} = {python_eq}")

    return {
        "input": "\n".join(summary_lines),
        "output": "\n".join(python_lines)
    }
```

### Target Dataset Size

| Dataset Size | Expected Quality | Training Time (1x A100) |
|--------------|------------------|-------------------------|
| 100 pairs    | Basic patterns   | ~10 minutes            |
| 500 pairs    | Good coverage    | ~30 minutes            |
| 1000 pairs   | Production-ready | ~1 hour                |
| 5000 pairs   | Excellent        | ~4 hours               |

---

## Phase 2: Data Format

### Training Data Schema (JSONL)

```jsonl
{"input": "LabVIEW VI: \"Get Settings Path\"\n\nOPERATIONS:\n  [1] Build Path...", "output": "import os\n\ndef get_settings_path():\n    ..."}
{"input": "LabVIEW VI: \"Read Config\"\n\nOPERATIONS:\n  [1] Open File...", "output": "import os\n\ndef read_config():\n    ..."}
```

### Prompt Template for Training

```
### Instruction:
Convert this LabVIEW VI summary to Python code.

### Input:
{input}

### Response:
{output}
```

### Validation Split

- 80% training
- 10% validation
- 10% test (held out for final evaluation)

---

## Phase 3: Fine-Tuning Setup

### Hardware Requirements

| Method | VRAM Required | Training Speed |
|--------|---------------|----------------|
| Full fine-tune (1.5B) | 12-16 GB | Fast |
| LoRA (1.5B-3B) | 6-8 GB | Medium |
| QLoRA 4-bit (3B-7B) | 4-6 GB | Slower |

### Recommended Base Models

1. **Qwen2.5-Coder-1.5B** - Best code quality for size
2. **Qwen2.5-Coder-3B** - Better if you have 8GB VRAM
3. **CodeLlama-7B** - If using QLoRA with more VRAM
4. **Phi-3-mini-4k** - Good instruction following

### Software Stack

```bash
# Create environment
python -m venv fine-tune-env
source fine-tune-env/bin/activate

# Install dependencies
pip install torch transformers datasets
pip install peft accelerate bitsandbytes  # For LoRA/QLoRA
pip install trl  # For supervised fine-tuning
pip install wandb  # For logging (optional)
```

---

## Phase 4: Training Script

### LoRA Fine-Tuning with PEFT

```python
"""Fine-tune Qwen2.5-Coder for LabVIEW-to-Python conversion."""

from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
)
from peft import LoraConfig, get_peft_model, TaskType
from trl import SFTTrainer

# Configuration
MODEL_NAME = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
OUTPUT_DIR = "./lvpy-lora"
DATA_PATH = "./training_data.jsonl"

# Load tokenizer and model
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    torch_dtype="auto",
    device_map="auto",
)

# LoRA configuration
lora_config = LoraConfig(
    task_type=TaskType.CAUSAL_LM,
    r=16,  # Rank - higher = more capacity, more VRAM
    lora_alpha=32,
    lora_dropout=0.05,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
)

model = get_peft_model(model, lora_config)
model.print_trainable_parameters()

# Load dataset
dataset = load_dataset("json", data_files=DATA_PATH, split="train")

def format_prompt(example):
    return f"""### Instruction:
Convert this LabVIEW VI summary to Python code.

### Input:
{example['input']}

### Response:
{example['output']}"""

# Training arguments
training_args = TrainingArguments(
    output_dir=OUTPUT_DIR,
    num_train_epochs=3,
    per_device_train_batch_size=4,
    gradient_accumulation_steps=4,
    learning_rate=2e-4,
    warmup_ratio=0.1,
    logging_steps=10,
    save_steps=100,
    fp16=True,  # Use bf16=True if supported
)

# Train
trainer = SFTTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset,
    formatting_func=format_prompt,
    max_seq_length=2048,
)

trainer.train()

# Save
model.save_pretrained(OUTPUT_DIR)
tokenizer.save_pretrained(OUTPUT_DIR)
```

### QLoRA for Lower VRAM (4-bit)

```python
from transformers import BitsAndBytesConfig

bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype="float16",
    bnb_4bit_use_double_quant=True,
)

model = AutoModelForCausalLM.from_pretrained(
    MODEL_NAME,
    quantization_config=bnb_config,
    device_map="auto",
)
```

---

## Phase 5: Export to Ollama

### Convert to GGUF

```bash
# Clone llama.cpp
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp

# Convert LoRA-merged model to GGUF
python convert_hf_to_gguf.py ../lvpy-lora --outfile lvpy-coder.gguf

# Quantize (optional, for smaller size)
./llama-quantize lvpy-coder.gguf lvpy-coder-q4_k_m.gguf Q4_K_M
```

### Create Ollama Model

```bash
# Create Modelfile
cat > Modelfile << 'EOF'
FROM ./lvpy-coder-q4_k_m.gguf

PARAMETER temperature 0.1
PARAMETER num_ctx 4096

SYSTEM """You convert LabVIEW VI summaries to Python code. Output only Python code, no explanations."""
EOF

# Import to Ollama
ollama create lvpy-coder -f Modelfile

# Test
ollama run lvpy-coder "LabVIEW VI: \"Test\"..."
```

---

## Phase 6: Integration with lvpy

### Update LLM Config

```python
# In src/lvpy/llm.py

# Add fine-tuned model as default
DEFAULT_MODEL = "lvpy-coder"  # Your fine-tuned model

class LLMConfig:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.1,
        ...
    ):
        ...
```

### Benchmark Script

```python
"""Benchmark fine-tuned model against base models."""

import time
from lvpy import convert_xml
from lvpy.llm import LLMConfig

TEST_VIS = [
    "samples/Get Settings Path_BDHb.xml",
    "samples/Get Platform Path Separator_BDHb.xml",
    # ... more test cases
]

MODELS = [
    "lvpy-coder",        # Fine-tuned
    "qwen2.5-coder:7b",  # Base 7B
    "qwen2.5-coder:14b", # Base 14B
]

for model in MODELS:
    config = LLMConfig(model=model)

    total_time = 0
    correct = 0

    for vi_path in TEST_VIS:
        start = time.time()
        result = convert_xml(vi_path, llm_config=config)
        elapsed = time.time() - start
        total_time += elapsed

        # Manual or automated correctness check
        # correct += evaluate(result, expected)

    print(f"{model}: {total_time:.1f}s total, {correct}/{len(TEST_VIS)} correct")
```

---

## Phase 7: Evaluation Metrics

### Automated Checks

1. **Syntax validity:** Does the output parse as Python?
   ```python
   import ast
   try:
       ast.parse(output)
       syntax_valid = True
   except SyntaxError:
       syntax_valid = False
   ```

2. **Import coverage:** Are required imports present?
   ```python
   required = {"os", "pathlib"}
   imports = extract_imports(output)
   coverage = len(required & imports) / len(required)
   ```

3. **Function structure:** Does it define a callable function?

### Manual Review Checklist

- [ ] Correct system directory mapping
- [ ] Proper path building/splitting
- [ ] Error handling where appropriate
- [ ] No hallucinated functionality
- [ ] Matches VI semantics

---

## Quick Start Checklist

1. [ ] Collect 100+ VI XML files
2. [ ] Generate summaries with `lvpy summarize`
3. [ ] Generate correct Python with Claude API
4. [ ] Format as JSONL training data
5. [ ] Run LoRA fine-tuning script
6. [ ] Convert to GGUF and import to Ollama
7. [ ] Benchmark against base models
8. [ ] Iterate on training data based on failures

---

## Resources

- [PEFT Documentation](https://huggingface.co/docs/peft)
- [TRL - Transformer Reinforcement Learning](https://huggingface.co/docs/trl)
- [Qwen2.5-Coder Models](https://huggingface.co/Qwen)
- [llama.cpp GGUF Conversion](https://github.com/ggerganov/llama.cpp)
- [Ollama Custom Models](https://ollama.com/blog/import-gguf)

---

## Estimated Costs

| Resource | Cost |
|----------|------|
| Claude API (1000 training pairs) | ~$5-10 |
| GPU rental (A100, 4 hours) | ~$8-16 |
| Local training (8GB GPU, overnight) | $0 (electricity) |

**Total estimated cost for production-ready model: $15-30**
