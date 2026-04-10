# MLOps on AWS China — Web Console 部署步骤

适配 AWS 中国区（cn-northwest-1 宁夏），完整架构见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

控制台地址：https://console.amazonaws.cn

---

## 前置条件

- AWS 中国区账号，已完成实名认证和 ICP 备案
- 本地已安装 Docker（用于构建推理镜像）
- 登录账号具备权限：IAM、S3、DynamoDB、Lambda、EventBridge、SNS、SageMaker、ECR、CloudWatch

---

## 第一阶段：创建 IAM 角色

### 1.1 创建 SageMaker 执行角色

1. **IAM** → **角色** → **创建角色**，使用案例选 **SageMaker**
2. 权限策略勾选 `AmazonSageMakerFullAccess` → 角色名 `SageMakerExecutionRole` → **创建**

### 1.2 给 SageMaker 角色添加 Lambda 调用权限

进入 `SageMakerExecutionRole` → **创建内联策略** → JSON：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "lambda:InvokeFunction",
    "Resource": "arn:aws-cn:lambda:cn-northwest-1:YOUR_ACCOUNT_ID:function:model-registry-*"
  }]
}
```

策略名 `SageMakerInvokeLambda` → **创建**

### 1.3 创建 Lambda 执行角色

1. **IAM** → **角色** → **创建角色**，使用案例选 **Lambda**
2. 权限策略勾选：`AmazonDynamoDBFullAccess`、`AmazonEventBridgeFullAccess`、`AmazonSageMakerFullAccess`
3. 角色名 `LambdaMLOpsRole` → **创建**
4. 进入角色 → **创建内联策略** → JSON：

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SageMakerExecutionRole"
    },
    {
      "Effect": "Allow",
      "Action": "sagemaker:StartPipelineExecution",
      "Resource": "*"
    }
  ]
}
```

策略名 `LambdaMLOpsPolicy` → **创建**

5. 再创建一个内联策略，添加 S3 读取权限（监控 Lambda 需要读取 Data Capture 数据）→ JSON：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "s3:GetObject",
      "s3:ListBucket"
    ],
    "Resource": [
      "arn:aws-cn:s3:::ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1",
      "arn:aws-cn:s3:::ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1/*"
    ]
  }]
}
```

策略名 `LambdaS3ReadAccess` → **创建**

---

## 第二阶段：创建 S3 存储桶

1. **S3** → **创建存储桶**，名称 `ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1`，区域 **cn-northwest-1**
2. 开启 **版本控制**，加密 **SSE-S3** → **创建**
3. 创建文件夹：`raw-data/`、`model-artifacts/`、`scripts/`、`data-capture/`

---

## 第三阶段：创建 DynamoDB 表

1. **DynamoDB** → **创建表**：表名 `ModelRegistry`，分区键 `model_name`，排序键 `version_id`，容量**按需**
2. 创建 GSI：分区键 `status`，排序键 `created_at`，索引名 `StatusIndex`

---

## 第四阶段：创建 EventBridge 自定义事件总线

**EventBridge** → **事件总线** → **创建**，名称 `model-registry-bus`

---

## 第五阶段：创建 Lambda 函数

> 每个函数必须修改 Handler 为 `文件名.lambda_handler`，否则报 `No module named 'lambda_function'`。

### 5.1 register_model

- 函数名：`model-registry-register`，Python 3.12，角色 `LambdaMLOpsRole`
- 代码：`lambda/register_model.py`，Handler：`register_model.lambda_handler`
- 环境变量：`MODEL_REGISTRY_TABLE=ModelRegistry`，`EVENT_BUS_NAME=model-registry-bus`
- 超时：30秒

### 5.2 approve_model

- 函数名：`model-registry-approve`，Python 3.12，角色 `LambdaMLOpsRole`
- 代码：`lambda/approve_model.py`，Handler：`approve_model.lambda_handler`
- 环境变量：`MODEL_REGISTRY_TABLE=ModelRegistry`，`EVENT_BUS_NAME=model-registry-bus`
- 超时：30秒

### 5.3 deploy_model

- 函数名：`model-registry-deploy`，Python 3.12，角色 `LambdaMLOpsRole`
- 代码：`lambda/deploy_model.py`，Handler：`deploy_model.lambda_handler`
- 环境变量：

  | 键 | 值 |
  |---|---|
  | `MODEL_REGISTRY_TABLE` | `ModelRegistry` |
  | `EVENT_BUS_NAME` | `model-registry-bus` |
  | `SAGEMAKER_EXECUTION_ROLE` | `arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SageMakerExecutionRole` |
  | `SAGEMAKER_ENDPOINT_NAME` | `ml-model-endpoint` |
  | `ENDPOINT_INSTANCE_TYPE` | `ml.m5.large` |
  | `ENDPOINT_INSTANCE_COUNT` | `1` |
  | `DEFAULT_INFERENCE_IMAGE` | `YOUR_ACCOUNT_ID.dkr.ecr.cn-northwest-1.amazonaws.com.cn/ml-inference:latest` |
  | `DATA_CAPTURE_S3_URI` | `s3://ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1/data-capture/` |

- 超时：60秒

### 5.4 alarm_retrain_trigger（监控触发重训）

- 函数名：`model-registry-alarm-retrain`，Python 3.12，角色 `LambdaMLOpsRole`
- 代码：`lambda/alarm_retrain_trigger.py`，Handler：`alarm_retrain_trigger.lambda_handler`
- 环境变量：`PIPELINE_NAME=mlops-fraud-detection`
- 超时：30秒

---

## 第六阶段：创建 EventBridge 规则

### 6.1 审批通过触发部署

**EventBridge** → **规则** → **创建**：
- 名称：`model-approved-trigger-deploy`，事件总线：`model-registry-bus`
- 事件模式：
  ```json
  {
    "source": ["custom.model-registry"],
    "detail-type": ["ModelApproved"]
  }
  ```
- 目标：Lambda → `model-registry-deploy`

> 重要：配置目标时，如果控制台自动创建了执行角色，需要删除该角色关联。Lambda 目标不需要执行角色，多余的 RoleArn 会导致调用失败。验证方法：Lambda → `model-registry-deploy` → 配置 → 权限 → 资源型策略语句中应有 `events.amazonaws.com` 的调用权限。

### 6.2 新数据上传触发重训

**前置条件：**
1. **S3** → 存储桶 → **属性** → **Amazon EventBridge** → **编辑** → **开启** → **保存**

**创建 EventBridge 规则：**

**EventBridge** → **规则** → **创建**：
- 名称：`new-data-trigger-retrain`，事件总线：**default**（注意：不是 model-registry-bus）
- 事件模式：
  ```json
  {
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": { "name": ["ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1"] },
      "object": { "key": [{ "prefix": "raw-data/" }] }
    }
  }
  ```
- 目标：Lambda → `model-registry-alarm-retrain`

> 重要：配置目标时不要勾选"创建新角色"，Lambda 目标不需要执行角色。如果控制台自动添加了 RoleArn，需要通过 CLI 删除旧目标并重新添加：
> ```bash
> # 查看当前目标
> aws events list-targets-by-rule --rule new-data-trigger-retrain --event-bus-name default --region cn-northwest-1
>
> # 如果目标中有 RoleArn，删除旧目标
> aws events remove-targets --rule new-data-trigger-retrain --event-bus-name default --ids "旧目标ID" --region cn-northwest-1
>
> # 添加新目标（不带 RoleArn）
> aws events put-targets --rule new-data-trigger-retrain --event-bus-name default \
>   --targets "Id=alarm-retrain-lambda,Arn=arn:aws-cn:lambda:cn-northwest-1:YOUR_ACCOUNT_ID:function:model-registry-alarm-retrain" \
>   --region cn-northwest-1
> ```

**添加 Lambda 调用权限：**

EventBridge 需要有权限调用 Lambda，通过以下命令添加：

```bash
aws lambda add-permission \
  --function-name model-registry-alarm-retrain \
  --statement-id AllowEventBridgeInvoke \
  --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn arn:aws-cn:events:cn-northwest-1:YOUR_ACCOUNT_ID:rule/new-data-trigger-retrain \
  --region cn-northwest-1
```

或在 Web Console 中通过 Lambda 添加触发器：
1. **Lambda** → `model-registry-alarm-retrain` → **配置** → **触发器** → **添加触发器**
2. 选择 **EventBridge (CloudWatch Events)** → **使用现有规则** → `new-data-trigger-retrain` → **添加**

**验证方法：**
- Lambda → `model-registry-alarm-retrain` → 配置 → 权限 → 资源型策略语句中应有 `events.amazonaws.com` 的调用权限

---

## 第七阶段：ECR 推理镜像准备

### 7.1 创建 ECR 存储库

**ECR 控制台** → **存储库** → **创建存储库**：
- 可见性：**私有**
- 名称：`ml-inference`
- **创建**

### 7.2 上传推理代码到 JupyterLab

在 SageMaker Studio JupyterLab 中，创建 `inference/` 目录，上传以下三个文件：

```
inference/
├── Dockerfile
├── inference.py   ← Flask app（/ping + /invocations），加载 model.joblib
└── serve          ← SageMaker 启动脚本（必须有）
```

### 7.3 在 JupyterLab Terminal 中构建并推送镜像

打开 Terminal，执行：

```bash
cd inference

# 设置变量（替换 YOUR_ACCOUNT_ID）
ACCOUNT_ID="YOUR_ACCOUNT_ID"
REGION="cn-northwest-1"
REPO_NAME="ml-inference"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com.cn/${REPO_NAME}:latest"

# 登录 ECR
aws ecr get-login-password --region ${REGION} | \
  docker login --username AWS --password-stdin \
  ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com.cn

# 构建镜像
docker build -t ${REPO_NAME} .

# 标记并推送
docker tag ${REPO_NAME}:latest ${IMAGE_URI}
docker push ${IMAGE_URI}

echo "镜像推送完成: ${IMAGE_URI}"
```

> 基础镜像用 `public.ecr.aws/docker/library/python:3.10-slim`。
> JupyterLab Space 建议使用 `ml.t3.medium` 或以上实例。

---

## 第八阶段：SageMaker Studio + Pipeline

### 8.1 进入 Studio

**SageMaker** → **域** → 已有域 → **启动** → **Studio** → **JupyterLab** → **Create Space** → `ml.t3.medium` → **Open JupyterLab**

### 8.2 准备数据

Terminal 中执行：

```bash
BUCKET="ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1"

python3 -c "
import pandas as pd, numpy as np
np.random.seed(42)
n = 1000
df = pd.DataFrame(np.random.randn(n, 5), columns=['f0','f1','f2','f3','f4'])
df['label'] = (df['f0'] + df['f1'] > 0).astype(int)
df.to_csv('sample_data.csv', index=False)
"
aws s3 cp sample_data.csv s3://${BUCKET}/raw-data/sample_data.csv
```

> raw-data/ 为空会报 `matched no files on s3`。

### 8.3 提交 Pipeline 定义

两种方式任选：

**方式一：代码提交**

上传 `pipeline/pipeline_definition.py`，修改 `ACCOUNT_ID` 和 `BUCKET` 后执行：

```bash
python pipeline_definition.py
```

> 改完代码后必须 Start execution 创建新执行，不能重试旧执行。

**方式二：Visual Pipeline Editor**

Studio → **Pipelines** → **Create pipeline** → 拖入 Processing / Training / Condition / Lambda 节点 → 连接 → **Save** → **Run**

### 8.4 触发 Pipeline

Studio → **Pipelines** → `mlops-fraud-detection` → **Start execution** → **Start**

### 8.5 Pipeline 步骤说明

| 步骤 | 脚本 | 作用 |
|------|------|------|
| DataProcessing | `preprocess.py` | 数据清洗、标准化、划分训练/验证集 |
| ModelTraining | `train.py` | 训练 RandomForest（SKLearn Estimator） |
| ModelEvaluation | `evaluate.py` | 验证集评估，输出 evaluation.json |
| CheckAccuracy | Pipeline 引擎 | accuracy >= 0.8 → 注册，否则结束 |
| RegisterModel | `register_model Lambda` | 写入 DynamoDB，状态 PendingApproval |

---

## 第九阶段：模型审批

### 9.1 查询待审批模型

**DynamoDB** → `ModelRegistry` → **查询** → 索引 `StatusIndex` → 分区键 `PendingApproval` → **运行**

### 9.2 执行审批

**Lambda** → `model-registry-approve` → **测试**：

```json
{
  "model_name": "fraud-detection",
  "version_id": "查到的version_id",
  "action": "Approved",
  "approved_by": "ml-engineer@company.com",
  "comment": "accuracy meets threshold"
}
```

> status 必须是 `PendingApproval`，否则返回 409。

### 9.3 验证自动部署

**Lambda** → `model-registry-deploy` → **监控** → **CloudWatch 日志**，确认：
```
Created SageMaker model: fraud-detection-xxx-...
Created new endpoint: ml-model-endpoint
Deployment triggered
```

---

## 第十阶段：确认 Endpoint

**SageMaker** → **推理** → **端点** → `ml-model-endpoint`，等状态变为 **InService**（约 5-10 分钟）。

Endpoint 部署时已自动开启 Data Capture，推理记录会自动写入 `s3://ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1/data-capture/`。

---

## 第十一阶段：配置预测分布偏移监控

### 11.1 创建 SNS Topic

**SNS** → **创建主题**，类型**标准**，名称 `mlops-retrain-trigger` → **创建**

进入主题 → **创建订阅**：协议 **Lambda**，端点 `model-registry-alarm-retrain` → **创建**

### 11.2 创建监控 Lambda

> 此 Lambda 定时读取 Data Capture 数据，统计预测分布，偏移超阈值时触发重训。

1. **Lambda** → **创建函数** → **从头开始创作**
   - 函数名：`model-registry-monitor`
   - 运行时：**Python 3.12**
   - 执行角色：`LambdaMLOpsRole`
2. 代码编辑器中新建文件 `monitor_drift.py`，粘贴 `lambda/monitor_drift.py` 完整代码
3. **运行时设置** → **编辑** → 处理程序改为 `monitor_drift.lambda_handler` → **保存**
4. **配置** → **环境变量**：
   - `DATA_CAPTURE_S3_URI` = `s3://ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1/data-capture/`
   - `PIPELINE_NAME` = `mlops-fraud-detection`
   - `BASELINE_POSITIVE_RATE` = `0.48`（训练时预测为 1 的比例）
   - `DRIFT_THRESHOLD` = `0.15`（偏移超过 15% 触发重训）
5. **常规配置** → 超时改为 `60秒` → **Deploy**

### 11.3 创建 EventBridge Scheduler 执行角色

1. **IAM** → **角色** → **创建角色** → **自定义信任策略**：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "scheduler.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
```

2. **下一步** → **创建策略**（新标签页）→ JSON：

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "lambda:InvokeFunction",
    "Resource": "arn:aws-cn:lambda:cn-northwest-1:YOUR_ACCOUNT_ID:function:model-registry-monitor"
  }]
}
```

策略名 `EventBridgeSchedulerInvokeLambda` → **创建**

3. 回到角色创建页面，刷新并勾选 `EventBridgeSchedulerInvokeLambda`
4. 角色名 `EventBridgeSchedulerRole` → **创建**

### 11.4 创建定时触发

**EventBridge** → **计划（Scheduler）** → **创建计划**：
- 名称：`mlops-daily-monitor`
- Cron：`0 3 * * ? *`（每天凌晨 3 点）
- 目标：Lambda → `InvokeFunction` → `model-registry-monitor`
- 执行角色：**使用现有角色** → `EventBridgeSchedulerRole`

### 11.5 演示验证

向 Endpoint 发送大量偏向某一类的请求，等待次日凌晨监控 Lambda 执行，检查是否自动触发 Pipeline。

---

## 第十二阶段：配置 Auto Scaling（可选）

**SageMaker** → **端点** → `ml-model-endpoint` → **配置自动扩缩**：最小 1，最大 4，目标值 1000 → **保存**

---

## 第十三阶段：配置推理入口（可选）

### 13.1 创建推理 Lambda

函数名 `ml-inference-proxy`，Python 3.12，角色 `LambdaMLOpsRole`：

```python
import json, boto3, os
runtime = boto3.client("sagemaker-runtime")
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

环境变量：`ENDPOINT_NAME=ml-model-endpoint`

### 13.2 创建 API Gateway

**API Gateway** → **REST API** → 资源 `/predict` → POST → 集成 `ml-inference-proxy` → 部署到 `prod`。

---

## 完整链路验证清单

| 步骤 | 验证方式 | 预期结果 |
|------|---------|---------|
| DynamoDB 表 | 探索表项目 | 表存在，StatusIndex GSI 存在 |
| EventBridge 总线 | EventBridge → 事件总线 | model-registry-bus 存在 |
| EventBridge 规则 | EventBridge → 规则 | 审批部署规则 + 新数据触发规则均启用 |
| S3 EventBridge 通知 | S3 → 属性 → EventBridge | 已开启 |
| Lambda Handler | 各 Lambda → 运行时设置 | Handler 已改为对应文件名 |
| S3 训练数据 | S3 → raw-data/ | sample_data.csv 存在 |
| Pipeline 执行 | Studio → Pipelines → 执行记录 | 所有步骤绿色完成 |
| DynamoDB 记录 | StatusIndex 查询 PendingApproval | 存在待审批记录 |
| 审批执行 | approve Lambda CloudWatch 日志 | 有 Published ModelApproved event |
| 部署触发 | deploy Lambda CloudWatch 日志 | 有 Deployment triggered |
| Endpoint 状态 | SageMaker → 端点 | InService |
| Data Capture | S3 → ml-model-artifacts-YOUR_ACCOUNT_ID-cn-northwest-1/data-capture/ | 推理后有 jsonl 文件生成 |
| 新数据触发 | 上传新 CSV 到 raw-data/ | Pipeline 自动启动 |

---

## 常见问题

| 错误 | 原因 | 解决 |
|------|------|------|
| `No module named 'lambda_function'` | Handler 未修改 | 改为 `文件名.lambda_handler` |
| `matched no files on s3` | raw-data/ 为空 | 上传训练数据 |
| `Float types are not supported` | DynamoDB 不支持 float | register_model.py 已用 Decimal 转换 |
| `Object of type Decimal is not JSON serializable` | DynamoDB 读出 Decimal | approve_model.py 已加 `_json_default` |
| `exec train failed: No such file or directory` | 用了自定义镜像 | 改用 SKLearn Estimator |
| `CannotStartContainerError` | 没有 serve 脚本 | inference/ 加 serve 文件 |
| Pipeline 重试后还是旧错误 | 旧执行快照 | 必须 Start execution 创建新执行 |
| 审批后部署未触发 | status 不是 PendingApproval | 改回 PendingApproval 再审批 |
| 新数据上传未触发 Pipeline | S3 未开启 EventBridge 通知 或 EventBridge 规则配置错误 | S3 属性 → EventBridge → 开启；或使用手动触发方式测试 |
| EventBridge Scheduler 报 `must allow AWS EventBridge Scheduler to assume the role` | 执行角色信任策略不正确 | 信任策略的 Principal.Service 必须是 `scheduler.amazonaws.com`（不是 `events.amazonaws.com`） |
| EventBridge 规则配置了 Lambda 目标但未触发 | 1. Lambda 缺少资源策略 2. 目标配置多了 RoleArn | 1. 通过 `aws lambda add-permission` 添加权限 2. 删除目标中的 RoleArn，Lambda 目标不需要执行角色 |
| 监控 Lambda 无日志输出 | 环境变量未配置或 S3 权限不足 | 1. 检查 `DATA_CAPTURE_S3_URI` 环境变量 2. 给 `LambdaMLOpsRole` 添加 S3 读取权限 |
| `RetrainCount parameter not present in pipeline` | alarm_retrain_trigger Lambda 传了不存在的参数 | 移除 `PipelineParameters` 中的 `RetrainCount` |
