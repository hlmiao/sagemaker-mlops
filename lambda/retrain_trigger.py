"""
retrain_trigger.py
Lambda: 评估未达标时自动触发 Pipeline 重新执行
由 Pipeline ConditionStep 的 else_steps 调用
"""
import os
import json
import boto3

sagemaker = boto3.client("sagemaker")

PIPELINE_NAME = os.environ.get("PIPELINE_NAME", "mlops-fraud-detection")
MAX_RETRAIN_COUNT = int(os.environ.get("MAX_RETRAIN_COUNT", "3"))


def lambda_handler(event, context):
    """
    event 示例（由 Pipeline LambdaStep 传入）:
    {
        "model_name": "fraud-detection",
        "accuracy": 0.72,
        "retrain_count": 1      # 当前已重训次数，防止无限循环
    }
    """
    retrain_count = int(event.get("retrain_count", 0))
    accuracy = event.get("accuracy", 0)

    print(f"Accuracy {accuracy} below threshold. Retrain count: {retrain_count}")

    if retrain_count >= MAX_RETRAIN_COUNT:
        print(f"Max retrain count ({MAX_RETRAIN_COUNT}) reached. Stopping.")
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": f"Max retrain count reached, stopping retrain loop",
                "retrain_count": retrain_count
            })
        }

    # 触发 Pipeline 重新执行，传入递增的 retrain_count
    response = sagemaker.start_pipeline_execution(
        PipelineName=PIPELINE_NAME,
        PipelineParameters=[
            {"Name": "RetrainCount", "Value": str(retrain_count + 1)},
        ],
        ClientRequestToken=f"retrain-{retrain_count + 1}-{context.aws_request_id[:8]}",
    )

    execution_arn = response["PipelineExecutionArn"]
    print(f"Triggered retrain execution: {execution_arn}")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": "Retrain triggered",
            "execution_arn": execution_arn,
            "retrain_count": retrain_count + 1
        })
    }
