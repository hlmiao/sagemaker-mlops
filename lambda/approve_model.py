"""
approve_model.py
Lambda: 审批或拒绝模型版本，审批通过后发送 EventBridge 事件触发部署
触发方式: API Gateway (人工审批) 或自动化审批逻辑
"""
import json
import os
import boto3
from decimal import Decimal
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
events_client = boto3.client("events")

TABLE_NAME = os.environ["MODEL_REGISTRY_TABLE"]
EVENT_BUS_NAME = os.environ["EVENT_BUS_NAME"]
VALID_STATUSES = {"Approved", "Rejected"}


def lambda_handler(event, context):
    """
    event 示例:
    {
        "model_name": "fraud-detection",
        "version_id": "abc12345",
        "action": "Approved",          # Approved | Rejected
        "approved_by": "data-scientist@example.com",
        "comment": "metrics look good"
    }
    """
    model_name = event["model_name"]
    version_id = event["version_id"]
    action = event["action"]

    if action not in VALID_STATUSES:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Invalid action: {action}. Must be one of {VALID_STATUSES}"})
        }

    table = dynamodb.Table(TABLE_NAME)

    # 检查模型版本是否存在且处于 PendingApproval 状态
    response = table.get_item(Key={"model_name": model_name, "version_id": version_id})
    item = response.get("Item")
    if not item:
        return {"statusCode": 404, "body": json.dumps({"error": "Model version not found"})}
    if item["status"] != "PendingApproval":
        return {
            "statusCode": 409,
            "body": json.dumps({"error": f"Model is already in status: {item['status']}"})
        }

    # 更新状态
    now = datetime.now(timezone.utc).isoformat()
    table.update_item(
        Key={"model_name": model_name, "version_id": version_id},
        UpdateExpression="SET #s = :s, updated_at = :t, approved_by = :ab, approval_comment = :c",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={
            ":s": action,
            ":t": now,
            ":ab": event.get("approved_by", "system"),
            ":c": event.get("comment", ""),
        }
    )

    # 审批通过后发送 EventBridge 事件，触发部署 Lambda
    if action == "Approved":
        _publish_approval_event(item, event.get("approved_by", "system"))

    print(f"Model {model_name}:{version_id} -> {action}")
    return {
        "statusCode": 200,
        "body": json.dumps({"model_name": model_name, "version_id": version_id, "status": action})
    }


def _json_default(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def _publish_approval_event(model_item: dict, approved_by: str):
    """发布 ModelApproved 事件到 EventBridge"""
    detail = {
        "model_name": model_item["model_name"],
        "version_id": model_item["version_id"],
        "s3_model_uri": model_item["s3_model_uri"],
        "metrics": model_item.get("metrics", {}),
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    events_client.put_events(
        Entries=[{
            "Source": "custom.model-registry",
            "DetailType": "ModelApproved",
            "Detail": json.dumps(detail, default=_json_default),
            "EventBusName": EVENT_BUS_NAME,
        }]
    )
    print(f"Published ModelApproved event for {model_item['model_name']}:{model_item['version_id']}")
