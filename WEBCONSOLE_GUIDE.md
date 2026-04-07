# MLOps on AWS China — Web Console 部署步骤

基于架构图 "MLOps with SageMaker"，适配 AWS 中国区（cn-northwest-1 宁夏）。
用 S3 + DynamoDB + Lambda + EventBridge 替代不可用的 SageMaker Model Registry。

控制台地址：https://console.amazonaws.cn

---

## 架构说明

```
SageMaker Pipeline
[DataProcessing → ModelTraining → ModelEvaluation → CheckAccuracy → RegisterModel]
                                                                          │
                                                                          ▼
                                                               DynamoDB (PendingApproval)
                                                                          │
                                                               手动执行 approve_model Lambda
                                                                          │
                                                               DynamoDB (Approved)
                                                               EventBridge: ModelApproved
                                                                          │
                                                               deploy_model Lambda (自动)
                                                                          │
                                                               SageMaker Endpoint (InService)
```

> EventBridge 不轮询状态，审批通过后由 approve_model Lambda 主动发送事件触发部署，之后全自动。

---

## 前置条件

- AWS 中国区账号，已完成实名认证和 ICP 备案
- 本地已安装 Docker（用于构建推理镜像）
- 登录账号具备权限：IAM、S3、DynamoDB、Lambda、EventBridge、API Gateway、SageMaker、ECR、CloudWatch

---

## 第一阶段：创建 IAM 角色

### 1.1 创建 SageMaker 执行角色

1. 进入 **IAM** → **角色** → **创建角色**
2. 可信实体选 **AWS 服务**，使用案例选 **SageMaker** → 下一步
3. 权限策略勾选 `AmazonSageMakerFullAccess` → 下一步
4. 角色名称填写 `SageMakerExecutionRole` → **创建角色**

### 1.2 给 SageMaker 执行角色添加 Lambda 调用权限

> Pipeline 的 RegisterModel 步骤需要调用 Lambda，必须提前加好，否则 Pipeline 执行到该步骤会报权限错误。

1. 进入刚创建的 `SageMakerExecutionRole`
2. **添加权限** → **创建内联策略** → 切换到 **JSON**，粘贴：
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "lambda:InvokeFunction",
         "Resource": "arn:aws-cn:lambda:cn-northwest-1:YOUR_ACCOUNT_ID:function:model-registry-register"
       }
     ]
   }
   ```
3. 策略名称填 `SageMakerInvokeLambda` → **创建策略**

### 1.3 创建 Lambda 执行角色

1. **IAM** → **角色** → **创建角色**
2. 可信实体选 **AWS 服务**，使用案例选 **Lambda** → 下一步
3. 权限策略勾选：`AmazonDynamoDBFullAccess`、`AmazonEventBridgeFullAccess`、`AmazonSageMakerFullAccess`
4. 角色名称填写 `LambdaMLOpsRole` → **创建角色**
5. 进入该角色 → **添加权限** → **创建内联策略**，添加 `iam:PassRole`：
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": "iam:PassRole",
         "Resource": "arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SageMakerExecutionRole"
       }
     ]
   }
   ```
6. 策略名称填 `PassSageMakerRole` → **创建策略**

---

## 第二阶段：创建 S3 存储桶

1. 进入 **S3** → **创建存储桶**
2. 存储桶名称：`ml-model-artifacts-{账号ID}-cn-northwest-1`，区域选 **cn-northwest-1**
3. 开启 **存储桶版本控制**，加密选 **SSE-S3**
4. 点击 **创建存储桶**
5. 进入存储桶，创建以下文件夹：`raw-data/`、`model-artifacts/`、`scripts/`

---

## 第三阶段：创建 DynamoDB 表

1. 进入 **DynamoDB** → **创建表**
   - 表名：`ModelRegistry`
   - 分区键：`model_name`（字符串）
   - 排序键：`version_id`（字符串）
   - 容量模式：**按需**
2. 创建完成后，进入表 → **索引** → **创建全局二级索引（GSI）**：
   - 分区键：`status`（字符串）
   - 排序键：`created_at`（字符串）
   - 索引名称：`StatusIndex`

---

## 第四阶段：创建 EventBridge 自定义事件总线

1. 进入 **Amazon EventBridge** → **事件总线** → **创建事件总线**
2. 名称：`model-registry-bus` → **创建**

---

## 第五阶段：创建三个 Lambda 函数

> 注意：每个函数创建后必须修改处理程序（Handler），否则会报 `No module named 'lambda_function'` 错误。

### 5.1 创建 register_model Lambda

1. **Lambda** → **创建函数** → **从头开始创作**
   - 函数名称：`model-registry-register`
   - 运行时：**Python 3.12**
   - 执行角色：选择 `LambdaMLOpsRole`
2. 代码编辑器中新建文件 `register_model.py`，粘贴 `lambda/register_model.py` 完整代码
3. **运行时设置** → **编辑** → 处理程序改为 `register_model.lambda_handler` → **保存**
4. **配置** → **环境变量**：
   - `MODEL_REGISTRY_TABLE` = `ModelRegistry`
   - `EVENT_BUS_NAME` = `model-registry-bus`
5. **常规配置** → 超时改为 `30秒` → **Deploy**

### 5.2 创建 approve_model Lambda

1. 函数名称：`model-registry-approve`，运行时 Python 3.12，执行角色 `LambdaMLOpsRole`
2. 新建 `approve_model.py`，粘贴 `lambda/approve_model.py` 完整代码
3. 处理程序改为 `approve_model.lambda_handler`
4. 环境变量：`MODEL_REGISTRY_TABLE` = `ModelRegistry`，`EVENT_BUS_NAME` = `model-registry-bus`
5. 超时 `30秒` → **Deploy**

### 5.3 创建 deploy_model Lambda

1. 函数名称：`model-registry-deploy`，运行时 Python 3.12，执行角色 `LambdaMLOpsRole`
2. 新建 `deploy_model.py`，粘贴 `lambda/deploy_model.py` 完整代码
3. 处理程序改为 `deploy_model.lambda_handler`
4. 环境变量：

   | 键 | 值 |
   |---|---|
   | `MODEL_REGISTRY_TABLE` | `ModelRegistry` |
   | `EVENT_BUS_NAME` | `model-registry-bus` |
   | `SAGEMAKER_EXECUTION_ROLE` | `arn:aws-cn:iam::YOUR_ACCOUNT_ID:role/SageMakerExecutionRole` |
   | `SAGEMAKER_ENDPOINT_NAME` | `ml-model-endpoint` |
   | `ENDPOINT_INSTANCE_TYPE` | `ml.m5.large` |
   | `ENDPOINT_INSTANCE_COUNT` | `1` |
   | `DEFAULT_INFERENCE_IMAGE` | `YOUR_ACCOUNT_ID.dkr.ecr.cn-northwest-1.amazonaws.com.cn/ml-inference:latest` |

5. 超时 `60秒` → **Deploy**

---

## 第六阶段：创建 EventBridge 规则

1. **EventBridge** → **规则** → **创建规则**
   - 名称：`model-approved-trigger-deploy`
   - 事件总线：选择 `model-registry-bus`（不是 default）
2. 事件模式选 **自定义模式**：
   ```json
   {
     "source": ["custom.model-registry"],
     "detail-type": ["ModelApproved"]
   }
   ```
3. 目标选 **Lambda 函数** → `model-registry-deploy` → **创建规则**

---

## 第七阶段：ECR 推理镜像准备

推理镜像需要支持 SageMaker 的 `serve` 启动命令，目录结构：

```
inference/
├── Dockerfile
├── inference.py   ← Flask app，提供 /ping 和 /invocations
└── serve          ← SageMaker 用此脚本启动容器（必须有）
```

在本地 `model-registry/inference/` 目录执行：

```bash
# 1. 登录 ECR
aws ecr get-login-password --region cn-northwest-1 | \
  docker login --username AWS --password-stdin \
  YOUR_ACCOUNT_ID.dkr.ecr.cn-northwest-1.amazonaws.com.cn

# 2. 在 ECR 控制台创建存储库 ml-inference（私有）

# 3. Build 并推送
docker build -t ml-inference .
docker tag ml-inference:latest \
  YOUR_ACCOUNT_ID.dkr.ecr.cn-northwest-1.amazonaws.com.cn/ml-inference:latest
docker push \
  YOUR_ACCOUNT_ID.dkr.ecr.cn-northwest-1.amazonaws.com.cn/ml-inference:latest
```

> 基础镜像使用 `public.ecr.aws/docker/library/python:3.10-slim`，中国区网络稳定。
> 不要用 SageMaker 官方 ECR 基础镜像，中国区拉取不稳定。

---

## 第八阶段：SageMaker Studio + Pipeline 配置

### 8.1 进入 SageMaker Studio

1. **SageMaker** → **域（Domains）** → 点击已有域 → **用户配置文件** → **启动** → **Studio**
2. Studio 左侧 → **JupyterLab** → **Create JupyterLab Space**，实例类型 `ml.t3.medium` → **Run**
3. 等 Space 启动后点击 **Open JupyterLab**

### 8.2 上传脚本并准备数据

打开 Terminal（**File** → **New** → **Terminal**）：

```bash
BUCKET="YOUR_BUCKET"

# 上传 Pipeline 脚本到 S3
aws s3 cp preprocess.py s3://${BUCKET}/scripts/preprocess.py
aws s3 cp train.py      s3://${BUCKET}/scripts/train.py
aws s3 cp evaluate.py   s3://${BUCKET}/scripts/evaluate.py

# 生成示例训练数据（没有真实数据时使用）
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

> 如果 `raw-data/` 目录为空，Pipeline 的 DataProcessing 步骤会报 `matched no files on s3` 错误。

### 8.3 提交 Pipeline 定义

上传 `pipeline/pipeline_definition.py` 到 JupyterLab，修改顶部变量后在 Terminal 执行：

```bash
python pipeline_definition.py
```

成功输出：
```
Pipeline upserted successfully
Pipeline ARN: arn:aws-cn:sagemaker:cn-northwest-1:YOUR_ACCOUNT_ID:pipeline/mlops-fraud-detection
```

> 只需执行一次。Pipeline 结构变更时重新执行，之后必须 **Start execution** 创建新执行，不能重试旧执行（旧执行快照里是旧定义）。

### 8.4 在 Studio 图形界面触发 Pipeline

1. Studio 左侧 → **Pipelines** → `mlops-fraud-detection`
2. 查看 DAG：`DataProcessing → ModelTraining → ModelEvaluation → CheckAccuracy → RegisterModel`
3. **Start execution** → 参数保持默认（AccuracyThreshold: 0.8）→ **Start**
4. 点击执行记录，实时查看各步骤状态

### 8.5 Pipeline 各步骤说明

| 步骤 | 脚本 | 作用 |
|------|------|------|
| DataProcessing | `preprocess.py` | 读取 S3 原始数据，标准化，划分训练/验证集 |
| ModelTraining | `train.py` | 训练 RandomForest 模型，用 SKLearn Estimator |
| ModelEvaluation | `evaluate.py` | 在验证集评估，输出 evaluation.json |
| CheckAccuracy | Pipeline 引擎 | accuracy >= 0.8 才继续，否则结束 |
| RegisterModel | `register_model Lambda` | 写入 DynamoDB，状态 PendingApproval |

---

## 第九阶段：模型审批

Pipeline 执行完成后，模型状态为 `PendingApproval`。

### 9.1 查询待审批模型

**DynamoDB** → `ModelRegistry` → **探索表项目** → **查询** → 索引选 `StatusIndex` → 分区键填 `PendingApproval` → **运行**，记录 `version_id`。

### 9.2 执行审批

**Lambda** → `model-registry-approve` → **测试** → 创建新事件：

```json
{
  "model_name": "fraud-detection",
  "version_id": "这里填查到的version_id",
  "action": "Approved",
  "approved_by": "ml-engineer@company.com",
  "comment": "accuracy meets threshold"
}
```

点击 **测试**，返回 200 表示成功。

> 注意：如果之前手动改过 DynamoDB 的 status 字段，Lambda 会因为状态不是 `PendingApproval` 而返回 409，不会触发部署。需要先把 status 改回 `PendingApproval` 再执行审批。

### 9.3 验证自动部署触发

**Lambda** → `model-registry-deploy` → **监控** → **查看 CloudWatch 日志**，确认看到：

```
Created SageMaker model: fraud-detection-xxx-...
Created EndpointConfig: fraud-detection-xxx-...-config
Created new endpoint: ml-model-endpoint
Deployment triggered: fraud-detection:xxx -> endpoint: ml-model-endpoint
```

---

## 第十阶段：确认 Endpoint 状态

**SageMaker** → **推理** → **端点** → `ml-model-endpoint`，等状态变为 **InService**（约 5-10 分钟）。

---

## 第十一阶段：配置 Auto Scaling（可选）

1. **SageMaker** → **端点** → `ml-model-endpoint`
2. **端点运行时设置** → 变体 `AllTraffic` → **配置自动扩缩**
   - 最小实例数：`1`，最大实例数：`4`，目标值：`1000`
3. **保存**

---

## 第十二阶段：配置推理入口（可选）

如需对外提供推理 API，创建推理 Lambda + API Gateway：

### 12.1 创建推理 Lambda

**Lambda** → **创建函数** → 函数名 `ml-inference-proxy`，Python 3.12，执行角色 `LambdaMLOpsRole`：

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

环境变量：`ENDPOINT_NAME` = `ml-model-endpoint`

### 12.2 创建 API Gateway

**API Gateway** → **REST API** → 创建资源 `/predict` → POST 方法 → 集成 `ml-inference-proxy` → 部署到 `prod` 阶段。

---

## 第十三阶段：配置 CloudWatch 告警（可选）

**CloudWatch** → **告警** → **创建告警** → 指标选 SageMaker `ml-model-endpoint` 的 `Invocation4XXErrors` → 阈值 `10` → 通知 SNS 邮件。

---

## 完整链路验证清单

| 步骤 | 验证方式 | 预期结果 |
|------|---------|---------|
| DynamoDB 表 | 探索表项目 | 表存在，StatusIndex GSI 存在 |
| EventBridge 总线 | EventBridge → 事件总线 | model-registry-bus 存在 |
| EventBridge 规则 | EventBridge → 规则 | model-approved-trigger-deploy 已启用，事件总线是 model-registry-bus |
| Lambda Handler | 各 Lambda → 运行时设置 | Handler 已改为对应文件名，非默认 lambda_function |
| S3 训练数据 | S3 → raw-data/ | sample_data.csv 存在 |
| Pipeline 执行 | Studio → Pipelines → 执行记录 | 所有步骤绿色完成 |
| DynamoDB 记录 | StatusIndex 查询 PendingApproval | 存在待审批记录 |
| 审批执行 | approve Lambda CloudWatch 日志 | 有 Published ModelApproved event 日志 |
| 部署触发 | deploy Lambda CloudWatch 日志 | 有 Deployment triggered 日志 |
| Endpoint 状态 | SageMaker → 端点 | ml-model-endpoint 状态 InService |

---

## 常见问题

| 错误 | 原因 | 解决 |
|------|------|------|
| `No module named 'lambda_function'` | Handler 未修改 | 运行时设置改为 `文件名.lambda_handler` |
| `No module named 'approve_model'` | 代码文件名不对 | Lambda 编辑器里新建对应 .py 文件并粘贴代码 |
| `matched no files on s3` | raw-data/ 目录为空 | 上传训练数据到 S3 |
| `Float types are not supported` | DynamoDB 不支持 float | register_model.py 已用 Decimal 转换 |
| `Object of type Decimal is not JSON serializable` | DynamoDB 读出 Decimal 无法序列化 | approve_model.py 已加 `_json_default` 处理 |
| `exec train failed: No such file or directory` | Estimator 用了自定义镜像但没有 train.py | 改用 SKLearn Estimator，自动处理脚本注入 |
| `CannotStartContainerError` | 推理镜像没有 serve 脚本 | inference/ 目录加 serve 文件，重新 build 推送 |
| Pipeline 重试后还是旧错误 | 重试用的是旧执行快照 | 改完代码后必须 Start execution 创建新执行 |
| 审批后部署未触发 | status 不是 PendingApproval | 把 DynamoDB status 改回 PendingApproval 再审批 |
