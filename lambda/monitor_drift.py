"""
monitor_drift.py
Lambda: 读取 Data Capture 数据，检测预测分布偏移
偏移超过阈值时自动触发 Pipeline 重训
触发方式: EventBridge Scheduler 每天执行
"""
import os
import json
import boto3
from datetime import datetime, timedelta, timezone

s3 = boto3.client("s3")
sagemaker = boto3.client("sagemaker")

PIPELINE_NAME = os.environ.get("PIPELINE_NAME", "mlops-fraud-detection")
# 训练时预测为正类的基准比例（从训练数据统计得出）
BASELINE_POSITIVE_RATE = float(os.environ.get("BASELINE_POSITIVE_RATE", "0.48"))
# 偏移阈值：实际正类比例与基准差异超过此值则触发重训
DRIFT_THRESHOLD = float(os.environ.get("DRIFT_THRESHOLD", "0.15"))


def lambda_handler(event, context):
    print(f"Monitor drift Lambda started, event: {json.dumps(event)}")
    
    # 从环境变量解析 S3 路径
    capture_uri = os.environ.get("DATA_CAPTURE_S3_URI", "")
    print(f"DATA_CAPTURE_S3_URI: {capture_uri}")
    
    if not capture_uri:
        print("ERROR: DATA_CAPTURE_S3_URI not set, skipping")
        return {"statusCode": 200, "body": "skipped"}

    bucket, prefix = _parse_s3_uri(capture_uri)
    print(f"Parsed S3 location - Bucket: {bucket}, Prefix: {prefix}")

    # 读取最近 24 小时的 Data Capture 文件
    print("Reading recent predictions from S3...")
    predictions = _read_recent_predictions(bucket, prefix, hours=24)
    print(f"Found {len(predictions)} predictions")

    if len(predictions) < 10:
        print(f"Only {len(predictions)} predictions in last 24h, not enough to evaluate")
        return {"statusCode": 200, "body": "not enough data"}

    # 统计预测为正类的比例
    positive_count = sum(1 for p in predictions if p == 1)
    actual_positive_rate = positive_count / len(predictions)
    drift = abs(actual_positive_rate - BASELINE_POSITIVE_RATE)

    print(f"Predictions: {len(predictions)}, Positive rate: {actual_positive_rate:.3f}, "
          f"Baseline: {BASELINE_POSITIVE_RATE:.3f}, Drift: {drift:.3f}, Threshold: {DRIFT_THRESHOLD}")

    if drift > DRIFT_THRESHOLD:
        print(f"Drift {drift:.3f} exceeds threshold {DRIFT_THRESHOLD}, triggering retrain")
        response = sagemaker.start_pipeline_execution(
            PipelineName=PIPELINE_NAME,
            PipelineExecutionDisplayName=f"retrain-drift-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        )
        print(f"Retrain triggered: {response['PipelineExecutionArn']}")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "drift": drift,
                "action": "retrain_triggered",
                "execution_arn": response["PipelineExecutionArn"],
            })
        }

    print("No significant drift detected")
    return {
        "statusCode": 200,
        "body": json.dumps({"drift": drift, "action": "none"})
    }


def _parse_s3_uri(uri):
    """s3://bucket/prefix/ -> (bucket, prefix)"""
    path = uri.replace("s3://", "")
    parts = path.split("/", 1)
    return parts[0], parts[1] if len(parts) > 1 else ""


def _read_recent_predictions(bucket, prefix, hours=24):
    """读取最近 N 小时的 Data Capture 文件，提取预测结果"""
    predictions = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    print(f"Looking for files after {cutoff}")

    try:
        paginator = s3.get_paginator("list_objects_v2")
        page_count = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            page_count += 1
            contents = page.get("Contents", [])
            print(f"Page {page_count}: found {len(contents)} objects")
            
            for obj in contents:
                if obj["LastModified"].replace(tzinfo=timezone.utc) < cutoff:
                    continue
                if not obj["Key"].endswith(".jsonl"):
                    continue

                print(f"Processing file: {obj['Key']}")
                try:
                    response = s3.get_object(Bucket=bucket, Key=obj["Key"])
                    lines = response["Body"].read().decode().strip().split("\n")
                    print(f"  Found {len(lines)} lines")
                    
                    for line in lines:
                        record = json.loads(line)
                        pred = _extract_prediction(record)
                        if pred is not None:
                            predictions.append(pred)
                except Exception as e:
                    print(f"Error reading {obj['Key']}: {e}")
        
        if page_count == 0:
            print(f"No objects found in s3://{bucket}/{prefix}")
    except Exception as e:
        print(f"Error listing S3 objects: {e}")

    return predictions


def _extract_prediction(record):
    """从 Data Capture 记录中提取预测值"""
    try:
        # Data Capture 格式: captureData.endpointOutput.data
        output_data = record.get("captureData", {}).get("endpointOutput", {}).get("data", "")
        if output_data:
            result = json.loads(output_data)
            # 适配 inference.py 的输出格式 {"prediction": [0]}
            pred = result.get("prediction", [None])[0]
            return int(pred) if pred is not None else None
    except (json.JSONDecodeError, ValueError, IndexError):
        pass
    return None
