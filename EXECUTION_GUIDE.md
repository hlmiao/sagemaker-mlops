# MLOps on AWS China 完整执行步骤

基于架构图 "MLOps with SageMaker"，适配 AWS 中国区（cn-north-1 北京 / cn-northwest-1 宁夏）。
用 S3 + DynamoDB + Lambda + EventBridge 替代不可用的 SageMaker Model Registry。

---

## 架构总览

```
Data Sources
    │
    ▼
SageMaker Studio Classic (Notebook)
    │  code artifacts        environments       model artifacts
    ▼                ▼                  ▼
AWS CodeCommit    Amazon ECR         Amazon S3
                                        │
                                        ▼
                              SageMaker Pipelines
                              [Data → Train → Evaluate]
                                        │ Pipeline 完成
                                        ▼
                              Lambda: register_model  ──► DynamoDB (PendingApproval)
                                        │
                              API Gateway (人工审批)
                                        │
                              Lambda: approve_model   ──► DynamoDB (Approved)
                                        │
                                        ▼
                              EventBridge: ModelApproved
                                        │
                              Lambda: deploy_model
                                        │
                    ┌───────────────────┴───────────────────┐
                    ▼                                       ▼
          SageMaker Endpoint                     DynamoDB (部署记录)
          (Auto Scaling Group)
                    │
          Lambda: Variant Weight
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
    API Gateway            CloudWatch Alarm
          │
        Users
```

---

## 前置条件

- AWS 中国区账号（北京或宁夏）
- 已完成 ICP 备案
- 本地安装：Python 3.10+、AWS CDK v2、AWS CLI（配置中国区 endpoint）
- AWS CLI 配置：

```bash
aws configure
# AWS Access Key ID: <your-key>
# AWS Secret Access Key: <your-secret>
# Default region name: cn-north-1        # 或 cn-northwest-1
# Default output format: json
```

---

## 第一阶段：基础设施部署（CDK）

### 1.1 安装依赖

```bash
cd model-registry
pip install -r requirements.txt

# 安装 CDK CLI（如未安装）
npm install -g aws-cdk
```

### 1.2 CDK Bootstrap（首次部署必须）

```bash
# 中国区 bootstrap 需要指定 qualifier，避免与全球区冲突
cdk bootstrap aws://YOUR_ACCOUNT_ID/cn-north-1 \
  --cloudformation-execution-policies arn:aws-cn:iam::aws:policy/AdministratorAccess
```

> 注意：中国区 ARN 前缀是 `arn:aws-cn`，不是 `arn:aws`

### 1.3 部署 Stack

```bash
cd model-registry/cdk
cdk deploy -c account=YOUR_ACCOUNT_ID -c region=cn-north-1
```

部署完成后记录输出中的：
- `ModelRegistryStack.ModelRegistryApiEndpoint` → API Gateway URL
- `ModelRegistryStack.ModelArtifactBucketName` → S3 bucket 名称

---

## 第二阶段：SageMaker Studio Classic 配置

### 2.1 创建 SageMaker Domain

```bash
aws sagemaker create-domain \
  --domain-name mlops-domain \
  --auth-mode IAM \
  --default-user-settings '{"ExecutionRole": "arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SageMakerExecutionRole"}' \
  --vpc-id vpc-xxxxxxxx \
  --subnet-ids subnet-xxxxxxxx \
  --region cn-north-1
```

> 中国区 SageMaker Domain 只支持 IAM 认证模式（不支持 SSO）

### 2.2 创建用户 Profile

```bash
aws sagemaker create-user-profile \
  --domain-id d-xxxxxxxxxx \
  --user-profile-name data-scientist \
  --region cn-north-1
```

### 2.3 打开 Studio Classic

在 AWS 控制台 → SageMaker → Domains → 选择 domain → Launch Studio Classic

---

## 第三阶段：代码仓库与镜像准备

### 3.1 创建 CodeCommit 仓库

```bash
# 创建训练代码仓库
aws codecommit create-repository \
  --repository-name mlops-training-code \
  --region cn-north-1

# 克隆并推送代码
git clone codecommit::cn-north-1://mlops-training-code
```

### 3.2 推送推理镜像到 ECR

```bash
# 创建 ECR 仓库
aws ecr create-repository \
  --repository-name ml-inference \
  --region cn-north-1

# 登录 ECR（中国区域名不同）
aws ecr get-login-password --region cn-north-1 | \
  docker login --username AWS --password-stdin \
  YOUR_ACCOUNT_ID.dkr.ecr.cn-north-1.amazonaws.com.cn

# 构建并推送镜像
docker build -t ml-inference .
docker tag ml-inference:latest \
  YOUR_ACCOUNT_ID.dkr.ecr.cn-north-1.amazonaws.com.cn/ml-inference:latest
docker push YOUR_ACCOUNT_ID.dkr.ecr.cn-north-1.amazonaws.com.cn/ml-inference:latest
```

### 3.3 更新 deploy_model Lambda 的推理镜像环境变量

```bash
aws lambda update-function-configuration \
  --function-name model-registry-deploy \
  --environment "Variables={
    DEFAULT_INFERENCE_IMAGE=YOUR_ACCOUNT_ID.dkr.ecr.cn-north-1.amazonaws.com.cn/ml-inference:latest,
    MODEL_REGISTRY_TABLE=ModelRegistry,
    EVENT_BUS_NAME=model-registry-bus,
    SAGEMAKER_EXECUTION_ROLE=arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/ModelRegistryStack-SageMakerExecutionRole,
    SAGEMAKER_ENDPOINT_NAME=ml-model-endpoint,
    ENDPOINT_INSTANCE_TYPE=ml.m5.large,
    ENDPOINT_INSTANCE_COUNT=1
  }" \
  --region cn-north-1
```

---

## 第四阶段：SageMaker Pipeline 配置

### 4.1 Pipeline 定义（在 Studio Classic Notebook 中执行）

```python
import boto3
import json
from sagemaker.workflow.pipeline import Pipeline
from sagemaker.workflow.steps import ProcessingStep, TrainingStep
from sagemaker.workflow.pipeline_context import PipelineSession
from sagemaker.workflow.lambda_step import LambdaStep, LambdaOutput
from sagemaker import get_execution_role

role = get_execution_role()
pipeline_session = PipelineSession()

# ── Step 1: 数据处理 ──────────────────────────────────────────────────
from sagemaker.sklearn.processing import SKLearnProcessor
from sagemaker.processing import ProcessingInput, ProcessingOutput

processor = SKLearnProcessor(
    framework_version="1.2-1",
    role=role,
    instance_type="ml.m5.xlarge",
    instance_count=1,
    sagemaker_session=pipeline_session,
)
step_process = ProcessingStep(
    name="DataProcessing",
    processor=processor,
    inputs=[ProcessingInput(source="s3://YOUR_BUCKET/raw-data/", destination="/opt/ml/processing/input")],
    outputs=[ProcessingOutput(output_name="train", source="/opt/ml/processing/output/train")],
    code="preprocess.py",
)

# ── Step 2: 模型训练 ──────────────────────────────────────────────────
from sagemaker.estimator import Estimator

estimator = Estimator(
    image_uri=f"YOUR_ACCOUNT_ID.dkr.ecr.cn-north-1.amazonaws.com.cn/ml-training:latest",
    role=role,
    instance_count=1,
    instance_type="ml.m5.xlarge",
    output_path="s3://YOUR_BUCKET/model-artifacts/",
    sagemaker_session=pipeline_session,
)
step_train = TrainingStep(
    name="ModelTraining",
    estimator=estimator,
    inputs={"train": sagemaker.inputs.TrainingInput(
        s3_data=step_process.properties.ProcessingOutputConfig.Outputs["train"].S3Output.S3Uri
    )},
)

# ── Step 3: 模型评估 + 注册到自建 Registry ────────────────────────────
from sagemaker.workflow.lambda_step import LambdaStep
from sagemaker.lambda_helper import Lambda

# 评估完成后调用 register_model Lambda
register_lambda = Lambda(function_arn="arn:aws-cn:lambda:cn-north-1:YOUR_ACCOUNT_ID:function:model-registry-register")

step_register = LambdaStep(
    name="RegisterModel",
    lambda_func=register_lambda,
    inputs={
        "model_name": "fraud-detection",
        "s3_model_uri": step_train.properties.ModelArtifacts.S3ModelArtifacts,
        "pipeline_run_id": "{{$.PipelineExecutionId}}",
        "metrics": {"accuracy": 0.95},  # 实际从评估步骤输出读取
    },
)

# ── 组装 Pipeline ─────────────────────────────────────────────────────
pipeline = Pipeline(
    name="mlops-fraud-detection",
    steps=[step_process, step_train, step_register],
    sagemaker_session=pipeline_session,
)
pipeline.upsert(role_arn=role)
```

### 4.2 手动触发 Pipeline（或通过 EventBridge Scheduler 定时触发）

```bash
# 手动触发
aws sagemaker start-pipeline-execution \
  --pipeline-name mlops-fraud-detection \
  --region cn-north-1

# 查看执行状态
aws sagemaker list-pipeline-executions \
  --pipeline-name mlops-fraud-detection \
  --region cn-north-1
```

### 4.3 配置 EventBridge Scheduler 定时触发（可选）

```bash
aws scheduler create-schedule \
  --name mlops-weekly-retrain \
  --schedule-expression "cron(0 2 ? * MON *)" \
  --target '{
    "Arn": "arn:aws-cn:sagemaker:cn-north-1:YOUR_ACCOUNT_ID:pipeline/mlops-fraud-detection",
    "RoleArn": "arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SchedulerRole",
    "SageMakerPipelineParameters": {"PipelineParameterList": []}
  }' \
  --flexible-time-window '{"Mode": "OFF"}' \
  --region cn-north-1
```

---

## 第五阶段：模型审批流程

Pipeline 执行完成后，模型版本状态为 `PendingApproval`，存储在 DynamoDB。

### 5.1 查询待审批模型

```bash
aws dynamodb query \
  --table-name ModelRegistry \
  --index-name StatusIndex \
  --key-condition-expression "#s = :s" \
  --expression-attribute-names '{"#s": "status"}' \
  --expression-attribute-values '{":s": {"S": "PendingApproval"}}' \
  --region cn-north-1
```

### 5.2 审批通过（通过 API Gateway）

```bash
# API_URL 从 CDK 部署输出获取
API_URL="https://xxxxxxxxxx.execute-api.cn-north-1.amazonaws.com.cn/prod"

curl -X POST \
  "${API_URL}/models/fraud-detection/versions/abc12345/approval" \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "fraud-detection",
    "version_id": "abc12345",
    "action": "Approved",
    "approved_by": "ml-engineer@company.com",
    "comment": "accuracy 0.95, meets threshold"
  }'
```

### 5.3 拒绝模型

```bash
curl -X POST \
  "${API_URL}/models/fraud-detection/versions/abc12345/approval" \
  -H "Content-Type: application/json" \
  -d '{
    "model_name": "fraud-detection",
    "version_id": "abc12345",
    "action": "Rejected",
    "approved_by": "ml-engineer@company.com",
    "comment": "f1 score below threshold"
  }'
```

审批通过后，`approve_model` Lambda 自动向 EventBridge 发送 `ModelApproved` 事件，触发部署流程。

---

## 第六阶段：自动部署流程（EventBridge → Lambda → SageMaker）

审批通过后全自动执行，无需人工干预：

```
EventBridge: ModelApproved
    │
    ▼
deploy_model Lambda
    ├── sagemaker.create_model()          # 注册推理模型
    ├── sagemaker.create_endpoint_config() # 新建 EndpointConfig
    └── sagemaker.update_endpoint()        # 蓝绿切换，零停机
```

### 6.1 监控部署状态

```bash
# 查看 Endpoint 状态（Updating → InService）
aws sagemaker describe-endpoint \
  --endpoint-name ml-model-endpoint \
  --region cn-north-1 \
  --query 'EndpointStatus'

# 查看 Lambda 执行日志
aws logs tail /aws/lambda/model-registry-deploy \
  --follow \
  --region cn-north-1
```

### 6.2 配置 Auto Scaling（Endpoint 部署完成后）

```bash
# 注册 Auto Scaling 目标
aws application-autoscaling register-scalable-target \
  --service-namespace sagemaker \
  --resource-id endpoint/ml-model-endpoint/variant/AllTraffic \
  --scalable-dimension sagemaker:variant:DesiredInstanceCount \
  --min-capacity 1 \
  --max-capacity 4 \
  --region cn-north-1

# 配置 Target Tracking 策略（基于 InvocationsPerInstance）
aws application-autoscaling put-scaling-policy \
  --policy-name mlops-endpoint-scaling \
  --service-namespace sagemaker \
  --resource-id endpoint/ml-model-endpoint/variant/AllTraffic \
  --scalable-dimension sagemaker:variant:DesiredInstanceCount \
  --policy-type TargetTrackingScaling \
  --target-tracking-scaling-policy-configuration '{
    "TargetValue": 1000.0,
    "PredefinedMetricSpecification": {
      "PredefinedMetricType": "SageMakerVariantInvocationsPerInstance"
    },
    "ScaleInCooldown": 300,
    "ScaleOutCooldown": 60
  }' \
  --region cn-north-1
```

---

## 第七阶段：推理服务配置

### 7.1 配置 Lambda Variant Weight（流量切换）

```python
# 用于 A/B 测试或金丝雀发布，调整各 variant 流量权重
import boto3
sagemaker = boto3.client("sagemaker", region_name="cn-north-1")

sagemaker.update_endpoint_weights_and_capacities(
    EndpointName="ml-model-endpoint",
    DesiredWeightsAndCapacities=[
        {"VariantName": "AllTraffic", "DesiredWeight": 1.0}
    ]
)
```

### 7.2 配置 API Gateway → Lambda → SageMaker Endpoint 推理链路

```bash
# 创建推理 Lambda（调用 SageMaker Endpoint）
# 该 Lambda 作为 API Gateway 后端，接收用户请求并转发到 SageMaker
aws lambda create-function \
  --function-name ml-inference-proxy \
  --runtime python3.12 \
  --handler inference_proxy.lambda_handler \
  --role arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/LambdaInferenceRole \
  --zip-file fileb://inference_proxy.zip \
  --environment "Variables={ENDPOINT_NAME=ml-model-endpoint}" \
  --region cn-north-1
```

推理 Lambda 示例：

```python
# inference_proxy.py
import json, boto3, os

runtime = boto3.client("sagemaker-runtime", region_name="cn-north-1")
ENDPOINT_NAME = os.environ["ENDPOINT_NAME"]

def lambda_handler(event, context):
    body = json.loads(event["body"])
    response = runtime.invoke_endpoint(
        EndpointName=ENDPOINT_NAME,
        ContentType="application/json",
        Body=json.dumps(body),
    )
    result = json.loads(response["Body"].read())
    return {"statusCode": 200, "body": json.dumps(result)}
```

### 7.3 配置 CloudWatch Alarm

```bash
# Endpoint 错误率告警
aws cloudwatch put-metric-alarm \
  --alarm-name mlops-endpoint-errors \
  --metric-name Invocation4XXErrors \
  --namespace AWS/SageMaker \
  --dimensions Name=EndpointName,Value=ml-model-endpoint \
  --statistic Sum \
  --period 300 \
  --threshold 10 \
  --comparison-operator GreaterThanThreshold \
  --evaluation-periods 2 \
  --alarm-actions arn:aws-cn:sns:cn-north-1:YOUR_ACCOUNT_ID:mlops-alerts \
  --region cn-north-1
```

---

## 完整数据流验证

按顺序执行以下验证命令，确认端到端链路正常：

```bash
# 1. 确认基础设施
aws dynamodb describe-table --table-name ModelRegistry --region cn-north-1
aws events describe-event-bus --name model-registry-bus --region cn-north-1

# 2. 手动触发注册（模拟 Pipeline 完成）
aws lambda invoke \
  --function-name model-registry-register \
  --payload '{"model_name":"test-model","s3_model_uri":"s3://YOUR_BUCKET/model.tar.gz","metrics":{"accuracy":0.95}}' \
  --cli-binary-format raw-in-base64-out \
  response.json \
  --region cn-north-1
cat response.json

# 3. 查询 DynamoDB 确认注册成功
aws dynamodb scan --table-name ModelRegistry --region cn-north-1

# 4. 审批模型（替换 version_id 为上一步返回值）
aws lambda invoke \
  --function-name model-registry-approve \
  --payload '{"model_name":"test-model","version_id":"YOUR_VERSION_ID","action":"Approved","approved_by":"test"}' \
  --cli-binary-format raw-in-base64-out \
  response.json \
  --region cn-north-1

# 5. 确认 EventBridge 触发了 deploy Lambda
aws logs tail /aws/lambda/model-registry-deploy --region cn-north-1

# 6. 确认 Endpoint 状态
aws sagemaker describe-endpoint \
  --endpoint-name ml-model-endpoint \
  --region cn-north-1
```

---

## 注意事项

| 项目 | 全球区 | 中国区 |
|------|--------|--------|
| ARN 前缀 | `arn:aws:` | `arn:aws-cn:` |
| ECR 域名 | `.amazonaws.com` | `.amazonaws.com.cn` |
| SageMaker Studio | 新版可用 | 仅 Studio Classic |
| Model Registry | 原生支持 | 本方案替代 |
| Serverless Inference | 支持 | 不支持，用标准 Endpoint |
| SageMaker Domain 认证 | IAM + SSO | 仅 IAM |
