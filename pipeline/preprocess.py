"""
preprocess.py
SageMaker Processing Step 执行脚本
输入: /opt/ml/processing/input/  (原始数据)
输出: /opt/ml/processing/output/train/  (训练集)
      /opt/ml/processing/output/validation/ (验证集)
"""
import os
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
import joblib

INPUT_DIR  = "/opt/ml/processing/input"
OUTPUT_TRAIN = "/opt/ml/processing/output/train"
OUTPUT_VAL   = "/opt/ml/processing/output/validation"

os.makedirs(OUTPUT_TRAIN, exist_ok=True)
os.makedirs(OUTPUT_VAL, exist_ok=True)

# 读取原始数据（CSV 格式，最后一列为 label）
input_files = [f for f in os.listdir(INPUT_DIR) if f.endswith(".csv")]
if not input_files:
    raise FileNotFoundError(f"No CSV files found in {INPUT_DIR}")

df = pd.concat([pd.read_csv(os.path.join(INPUT_DIR, f)) for f in input_files])
print(f"Loaded {len(df)} rows, columns: {list(df.columns)}")

# 分离特征和标签（假设最后一列是 label）
X = df.iloc[:, :-1]
y = df.iloc[:, -1]

# 标准化
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# 保存 scaler 供推理时使用
joblib.dump(scaler, os.path.join(OUTPUT_TRAIN, "scaler.joblib"))

# 划分训练集和验证集 (80/20)
X_train, X_val, y_train, y_val = train_test_split(
    X_scaled, y, test_size=0.2, random_state=42
)

# 保存为 CSV
train_df = pd.DataFrame(X_train)
train_df["label"] = y_train.values
train_df.to_csv(os.path.join(OUTPUT_TRAIN, "train.csv"), index=False)

val_df = pd.DataFrame(X_val)
val_df["label"] = y_val.values
val_df.to_csv(os.path.join(OUTPUT_VAL, "validation.csv"), index=False)

print(f"Train: {len(train_df)} rows -> {OUTPUT_TRAIN}/train.csv")
print(f"Validation: {len(val_df)} rows -> {OUTPUT_VAL}/validation.csv")
