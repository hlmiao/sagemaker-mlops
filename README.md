# MLOps on AWS China

基于 SageMaker Pipelines 的端到端 MLOps 方案，适配 AWS 中国区（cn-northwest-1 / cn-north-1）。

用 S3 + DynamoDB + Lambda + EventBridge 自建 Model Registry，替代中国区不可用的 SageMaker Model Registry。

---

## 架构

详细架构图见 [ARCHITECTURE.md](./ARCHITECTURE.md)

```
链路一：训练 → 部署
  S3 训练数据 → Pipeline [处理→训练→评估→门控] → 注册 → 人工审批 → 自动部署 Endpoint

链路二：重训触发
  触发方式 A：新数据上传到 S3 → EventBridge → 自动触发 Pipeline
  触发方式 B：Data Capture 预测分布偏移 → 监控 Lambda → 自动触发 Pipeline
```

## 功能

- 自动化训练流水线（数据处理 → 训练 → 评估）
- 质量门控（新模型 accuracy 达标才注册，未达标直接结束）
- 模型版本管理（S3 + DynamoDB 自建 Registry）
- 人工审批流程
- 审批通过后自动部署（蓝绿切换，零停机）
- Data Capture 自动记录推理输入输出
- 预测分布偏移检测，异常时自动触发重训
- 新数据上传自动触发重训

## 项目结构

```
model-registry/
├── lambda/
│   ├── register_model.py        # 注册模型版本到 DynamoDB
│   ├── approve_model.py         # 审批/拒绝模型，触发 EventBridge
│   ├── deploy_model.py          # 接收事件，部署 SageMaker Endpoint（含 Data Capture）
│   ├── monitor_drift.py         # 读取 Data Capture，检测预测分布偏移，触发重训
│   ├── alarm_retrain_trigger.py # 新数据上传/告警触发重训（支持 SNS 和 EventBridge S3 事件）
│   └── retrain_trigger.py       # Pipeline ConditionStep 未达标时的占位（当前未使用）
├── pipeline/
│   ├── pipeline_definition.py   # SageMaker Pipeline 定义
│   ├── preprocess.py            # 数据处理脚本
│   ├── train.py                 # 模型训练脚本
│   └── evaluate.py              # 模型评估脚本
├── inference/
│   ├── Dockerfile               # 推理容器镜像
│   ├── inference.py             # Flask 推理服务（/ping + /invocations）
│   └── serve                    # SageMaker 启动脚本
├── cdk/
│   ├── app.py
│   └── model_registry_stack.py  # CDK 基础设施定义（可选）
├── ARCHITECTURE.md              # 完整架构图
├── WEBCONSOLE_GUIDE.md          # Web 控制台部署步骤（推荐）
├── EXECUTION_GUIDE.md           # CLI 部署步骤
└── requirements.txt
```

## 快速开始

参考 [WEBCONSOLE_GUIDE.md](./WEBCONSOLE_GUIDE.md) 通过 AWS 控制台完成部署。

## 前置条件

- AWS 中国区账号（北京 cn-north-1 或宁夏 cn-northwest-1）
- 本地安装 Docker（构建推理镜像）
- Python 3.10+

## 中国区适配说明

| 功能 | 全球区 | 中国区方案 |
|------|--------|-----------|
| SageMaker Model Registry | 原生支持 | S3 + DynamoDB 自建 |
| SageMaker Model Monitor | 原生支持 | Data Capture + 自建监控 Lambda |
| SageMaker Studio | 新版可用 | 使用 JupyterLab Space + Visual Pipeline Editor |
| 推理镜像基础镜像 | SageMaker ECR | `public.ecr.aws/docker/library/python:3.10-slim` |
| Serverless Inference | 支持 | 不支持，使用标准 Endpoint |

## 部署注意事项

### EventBridge 权限配置

1. **EventBridge Scheduler** 和 **EventBridge Rules** 使用不同的服务主体：
   - Scheduler 信任策略：`scheduler.amazonaws.com`
   - Rules 信任策略：`events.amazonaws.com`
   - 不能混用，否则会报 `must allow AWS EventBridge Scheduler to assume the role`

2. **EventBridge Rules 的 Lambda 目标不需要执行角色（RoleArn）**：
   - Web Console 创建规则时可能自动添加 RoleArn，需要删除
   - Lambda 通过资源策略（Resource-based Policy）授权 EventBridge 调用
   - 如果目标中多了 RoleArn，EventBridge 无法正确调用 Lambda

3. **Lambda 资源策略**：
   - EventBridge 调用 Lambda 需要 Lambda 有对应的资源策略
   - 通过 `aws lambda add-permission` 或 Lambda 控制台添加触发器来自动添加

### IAM 角色

- `LambdaMLOpsRole` 需要额外的 S3 读取权限（监控 Lambda 读取 Data Capture 数据）
- `EventBridgeSchedulerRole` 信任策略必须是 `scheduler.amazonaws.com`
