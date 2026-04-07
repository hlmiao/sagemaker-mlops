"""
register_model.py
Lambda: 注册模型版本到 DynamoDB，模型 artifact 已上传至 S3
触发方式: SageMaker Pipeline 评估步骤完成后调用
"""
import json
import os
import uuid
import boto3
from decimal import Decimal
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ["MODEL_REGISTRY_TABLE"]


def _convert_floats(obj):
    """递归将 dict/list 中的 float 转为 Decimal，DynamoDB 不支持 float"""
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _convert_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_floats(i) for i in obj]
    return obj


def lambda_handler(event, context):
    """
    event 示例 (来自 SageMaker Pipeline LambdaStep):
    {
        "model_name": "fraud-detection",
        "model_version": "1.0.0",          # 可选，不传则自动生成
        "s3_model_uri": "s3://bucket/path/model.tar.gz",
        "pipeline_run_id": "xxx",
        "accuracy": 0.95,
        "tags": {
            "team": "ml-platform"
        }
    }
    """
    table = dynamodb.Table(TABLE_NAME)

    model_name = event["model_name"]
    version_id = event.get("model_version") or str(uuid.uuid4())[:8]
    # 主键: model_name, 排序键: version_id
    item = {
        "model_name": model_name,
        "version_id": version_id,
        "s3_model_uri": event["s3_model_uri"],
        "pipeline_run_id": event.get("pipeline_run_id", ""),
        "metrics": _convert_floats({"accuracy": event.get("accuracy")}),
        "tags": event.get("tags", {}),
        "status": "PendingApproval",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    table.put_item(Item=item)

    print(f"Registered model: {model_name} version: {version_id}")
    return {
        "statusCode": 200,
        "body": json.dumps({
            "model_name": model_name,
            "version_id": version_id,
            "status": "PendingApproval"
        })
    }