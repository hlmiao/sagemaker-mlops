from flask import Flask, request, jsonify, Response
import json

app = Flask(__name__)

@app.route("/ping", methods=["GET"])
def ping():
    return Response("OK", status=200)

@app.route("/invocations", methods=["POST"])
def invocations():
    data = request.get_json(force=True)
    # TODO: 替换为真实模型推理逻辑
    return jsonify({"prediction": [0], "input": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
