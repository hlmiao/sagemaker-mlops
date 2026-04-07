"""
evaluate.py
SageMaker Processing Step 执行脚本（评估阶段）
读取验证集和训练好的模型，输出评估指标 JSON
供 Pipeline 的 ConditionStep 判断是否注册模型
"""
import os
import json
import tarfile
import pandas as pd
import joblib
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score

MODEL_DIR  = "/opt/ml/processing/model"
VAL_DIR    = "/opt/ml/processing/validation"
OUTPUT_DIR = "/opt/ml/processing/evaluation"

os.makedirs(OUTPUT_DIR, exist_ok=True)

# 解压 model.tar.gz
model_tar = os.path.join(MODEL_DIR, "model.tar.gz")
with tarfile.open(model_tar) as t:
    t.extractall(MODEL_DIR)

model = joblib.load(os.path.join(MODEL_DIR, "model.joblib"))

# 读取验证集
val_files = [f for f in os.listdir(VAL_DIR) if f.endswith(".csv")]
df = pd.concat([pd.read_csv(os.path.join(VAL_DIR, f)) for f in val_files])

X = df.iloc[:, :-1].values
y = df.iloc[:, -1].values

y_pred = model.predict(X)

metrics = {
    "accuracy":  round(accuracy_score(y, y_pred), 4),
    "f1":        round(f1_score(y, y_pred, average="weighted"), 4),
    "precision": round(precision_score(y, y_pred, average="weighted"), 4),
    "recall":    round(recall_score(y, y_pred, average="weighted"), 4),
}
print(f"Evaluation metrics: {metrics}")

# 输出标准格式，供 SageMaker Clarify / ConditionStep 读取
report = {"metrics": [{"name": k, "value": v} for k, v in metrics.items()]}
with open(os.path.join(OUTPUT_DIR, "evaluation.json"), "w") as f:
    json.dump(report, f)

print(f"Evaluation report saved to {OUTPUT_DIR}/evaluation.json")
