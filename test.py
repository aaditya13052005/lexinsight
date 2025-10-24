import pandas as pd
import json

# 1️⃣ Load CSV
csv_file = "legal_combined_dataset.csv"
df = pd.read_csv(csv_file)

# 2️⃣ Create JSONL
jsonl_file = "legal_combined_dataset.jsonl"
with open(jsonl_file, "w", encoding="utf-8") as f:
    for _, row in df.iterrows():
        data = {
            "prompt": row["prompt"].strip(),
            "completion": row["completion"].strip()
        }
        f.write(json.dumps(data) + "\n")

print(f"✅ JSONL file created: {jsonl_file}")
