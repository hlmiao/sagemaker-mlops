"""
alarm_retrain_trigger.py
Lambda: 接收 CloudWatch Alarm SNS 通知，触发 Pipeline 重训
触发方式: SNS → Lambda
"""
import os
import json
import boto3

sagemaker = boto3.client("sagemaker")
PIPELINE_NAME = os.environ.get("PIPELINE_NAME", "mlops-fraud-detection")


def lambda_handler(event, context):
    print(f"Received event: {json.dumps(event)}")
    
    # 判断事件类型
    if "Records" in event and "Sns" in event["Records"][0]:
        # SNS 事件（CloudWatch Alarm）
        message = json.loads(event["Records"][0]["Sns"]["Message"])
        alarm_name = message.get("AlarmName", "unknown")
        new_state = message.get("NewStateValue", "")
        
        print(f"SNS event - Alarm: {alarm_name}, State: {new_state}")
        
        if new_state != "ALARM":
            print("Not in ALARM state, skipping retrain")
            return {"statusCode": 200, "body": "skipped"}
    
    elif "detail" in event and "bucket" in event.get("detail", {}):
        # EventBridge S3 事件
        bucket = event["detail"]["bucket"]["name"]
        key = event["detail"]["object"]["key"]
        print(f"S3 event - Bucket: {bucket}, Key: {key}")
    
    else:
        print("Unknown event type, triggering retrain anyway")
    
    print(f"Triggering pipeline: {PIPELINE_NAME}")

    # 触发 Pipeline 重训
    response = sagemaker.start_pipeline_execution(
        PipelineName=PIPELINE_NAME,
        PipelineExecutionDisplayName=f"retrain-alarm-triggered",
    )
    print(f"Retrain triggered: {response['PipelineExecutionArn']}")
    return {"statusCode": 200, "body": json.dumps({"execution_arn": response["PipelineExecutionArn"]})}
