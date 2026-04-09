"""
deploy_model.py
Lambda: 接收 EventBridge ModelApproved 事件，更新 SageMaker Endpoint
支持蓝绿部署：先创建新 EndpointConfig，再更新 Endpoint
"""
import json
import os
import boto3
from datetime import datetime, timezone

sagemaker = boto3.client("sagemaker")
dynamodb = boto3.resource("dynamodb")

TABLE_NAME = os.environ["MODEL_REGISTRY_TABLE"]
SAGEMAKER_EXECUTION_ROLE = os.environ["SAGEMAKER_EXECUTION_ROLE"]
ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT_NAME", "ml-model-endpoint")


def lambda_handler(event, context):
    """
    触发方式: EventBridge rule 匹配 source=custom.model-registry, detail-type=ModelApproved
    event["detail"] 包含 model_name, version_id, s3_model_uri 等
    """
    detail = event["detail"]
    model_name = detail["model_name"]
    version_id = detail["version_id"]
    s3_model_uri = detail["s3_model_uri"]

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    sm_model_name = f"{model_name}-{version_id}-{timestamp}"
    endpoint_config_name = f"{sm_model_name}-config"

    # 1. 创建 SageMaker Model
    _create_sagemaker_model(sm_model_name, s3_model_uri, model_name)

    # 2. 创建新的 EndpointConfig
    _create_endpoint_config(endpoint_config_name, sm_model_name)

    # 3. 更新或创建 Endpoint（蓝绿切换）
    _update_or_create_endpoint(endpoint_config_name)

    # 4. 更新 DynamoDB 记录部署信息
    _update_deployment_info(model_name, version_id, sm_model_name, endpoint_config_name)

    print(f"Deployment triggered: {model_name}:{version_id} -> endpoint: {ENDPOINT_NAME}")
    return {"statusCode": 200, "body": json.dumps({"endpoint": ENDPOINT_NAME, "model": sm_model_name})}


def _create_sagemaker_model(sm_model_name: str, s3_model_uri: str, model_name: str):
    """从 ECR 镜像 + S3 artifact 创建 SageMaker Model"""
    # 推理镜像从环境变量读取，支持不同模型使用不同镜像
    inference_image = os.environ.get(
        f"INFERENCE_IMAGE_{model_name.upper().replace('-', '_')}",
        os.environ["DEFAULT_INFERENCE_IMAGE"]
    )
    sagemaker.create_model(
        ModelName=sm_model_name,
        PrimaryContainer={
            "Image": inference_image,
            "ModelDataUrl": s3_model_uri,
            "Environment": {"SAGEMAKER_PROGRAM": "inference.py"},
        },
        ExecutionRoleArn=SAGEMAKER_EXECUTION_ROLE,
    )
    print(f"Created SageMaker model: {sm_model_name}")


def _create_endpoint_config(endpoint_config_name: str, sm_model_name: str):
    """创建 EndpointConfig，含 Data Capture 配置"""
    instance_type = os.environ.get("ENDPOINT_INSTANCE_TYPE", "ml.m5.large")
    instance_count = int(os.environ.get("ENDPOINT_INSTANCE_COUNT", "1"))
    data_capture_uri = os.environ.get("DATA_CAPTURE_S3_URI", "")

    config = {
        "EndpointConfigName": endpoint_config_name,
        "ProductionVariants": [{
            "VariantName": "AllTraffic",
            "ModelName": sm_model_name,
            "InitialInstanceCount": instance_count,
            "InstanceType": instance_type,
            "InitialVariantWeight": 1.0,
        }],
    }

    # 开启 Data Capture：自动记录推理输入输出到 S3
    if data_capture_uri:
        config["DataCaptureConfig"] = {
            "EnableCapture": True,
            "InitialSamplingPercentage": 100,
            "DestinationS3Uri": data_capture_uri,
            "CaptureOptions": [
                {"CaptureMode": "Input"},
                {"CaptureMode": "Output"},
            ],
            "CaptureContentTypeHeader": {
                "JsonContentTypes": ["application/json"],
            },
        }
        print(f"Data Capture enabled: {data_capture_uri}")

    sagemaker.create_endpoint_config(**config)
    print(f"Created EndpointConfig: {endpoint_config_name}")


def _update_or_create_endpoint(endpoint_config_name: str):
    """更新已有 Endpoint，若不存在则创建"""
    try:
        sagemaker.describe_endpoint(EndpointName=ENDPOINT_NAME)
        sagemaker.update_endpoint(
            EndpointName=ENDPOINT_NAME,
            EndpointConfigName=endpoint_config_name
        )
        print(f"Updated endpoint: {ENDPOINT_NAME}")
    except sagemaker.exceptions.ClientError as e:
        if "Could not find endpoint" in str(e) or "ValidationException" in str(e):
            sagemaker.create_endpoint(
                EndpointName=ENDPOINT_NAME,
                EndpointConfigName=endpoint_config_name
            )
            print(f"Created new endpoint: {ENDPOINT_NAME}")
        else:
            raise


def _update_deployment_info(model_name: str, version_id: str, sm_model_name: str, config_name: str):
    """记录部署信息到 DynamoDB"""
    table = dynamodb.Table(TABLE_NAME)
    table.update_item(
        Key={"model_name": model_name, "version_id": version_id},
        UpdateExpression="SET deployment_info = :d, updated_at = :t",
        ExpressionAttributeValues={
            ":d": {
                "sagemaker_model_name": sm_model_name,
                "endpoint_config_name": config_name,
                "endpoint_name": ENDPOINT_NAME,
                "deployed_at": datetime.now(timezone.utc).isoformat(),
            },
            ":t": datetime.now(timezone.utc).isoformat(),
        }
    )
