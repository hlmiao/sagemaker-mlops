"""
inference.py
Flask 推理服务：加载 model.tar.gz 中的 model.joblib，提供 /ping 和 /invocations 端点
SageMaker 会将 model.tar.gz 解压到 /opt/ml/model/
"""
import os
import json
import joblib
import numpy as np
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

MODEL_DIR = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
model = None


def load_model():
    global model
    model_path = os.path.join(MODEL_DIR, "model.joblib")
    if os.path.exists(model_path):
        model = joblib.load(model_path)
        print(f"Model loaded from {model_path}")
    else:
        print(f"WARNING: model not found at {model_path}, using dummy model")


@app.route("/ping", methods=["GET"])
def ping():
    """健康检查：SageMaker 用此端点判断容器是否就绪"""
    if model is None:
        load_model()
    status = 200 if model is not None else 503
    return Response("OK", status=status)


@app.route("/invocations", methods=["POST"])
def invocations():
    """推理端点：接收 JSON 输入，返回预测结果"""
    if model is None:
        load_model()

    data = request.get_json(force=True)
    instances = np.array(data.get("instances", []))

    if instances.ndim == 1:
        instances = instances.reshape(1, -1)

    predictions = model.predict(instances).tolist()
    probabilities = model.predict_proba(instances).tolist()

    return jsonify({
        "prediction": predictions,
        "probabilities": probabilities,
        "input": data,
    })


if __name__ == "__main__":
    load_model()
    app.run(host="0.0.0.0", port=8080)
