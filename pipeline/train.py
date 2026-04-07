"""
train.py
SageMaker Training Step 执行脚本
SageMaker 会自动将 S3 数据下载到 /opt/ml/input/data/train/
训练完成后将模型保存到 /opt/ml/model/，SageMaker 自动打包上传 S3
"""
import os
import argparse
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, f1_score

# SageMaker 标准路径
TRAIN_DIR  = os.environ.get("SM_CHANNEL_TRAIN", "/opt/ml/input/data/train")
MODEL_DIR  = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-estimators", type=int, default=100)
    parser.add_argument("--max-depth",    type=int, default=5)
    parser.add_argument("--random-state", type=int, default=42)
    return parser.parse_args()

def main():
    args = parse_args()

    # 读取训练数据
    train_files = [f for f in os.listdir(TRAIN_DIR) if f.endswith(".csv")]
    df = pd.concat([pd.read_csv(os.path.join(TRAIN_DIR, f)) for f in train_files])
    print(f"Training on {len(df)} rows")

    X = df.iloc[:, :-1].values
    y = df.iloc[:, -1].values

    # 训练模型
    model = RandomForestClassifier(
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        random_state=args.random_state,
    )
    model.fit(X, y)

    # 评估
    y_pred = model.predict(X)
    acc = accuracy_score(y, y_pred)
    f1  = f1_score(y, y_pred, average="weighted")
    print(f"Train accuracy: {acc:.4f}, F1: {f1:.4f}")

    # 保存模型到 SM_MODEL_DIR，SageMaker 会自动打包成 model.tar.gz 上传 S3
    os.makedirs(MODEL_DIR, exist_ok=True)
    joblib.dump(model, os.path.join(MODEL_DIR, "model.joblib"))
    print(f"Model saved to {MODEL_DIR}/model.joblib")

if __name__ == "__main__":
    main()
