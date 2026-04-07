# MLOps on AWS China

基于 SageMaker Pipelines 的端到端 MLOps 方案，适配 AWS 中国区（cn-northwest-1 / cn-north-1）。

用 S3 + DynamoDB + Lambda + EventBridge 自建 Model Registry，替代中国区不可用的 SageMaker Model Registry。

---

## 架构

```
SageMaker Pipeline
  DataProcessing → ModelTraining → ModelEvaluation → CheckAccuracy → RegisterModel
                                                                            │
                                                                     DynamoDB (PendingApproval)
                                                                            │
                                                                     人工审批 (Lambda)
                                                                            │
                                                                     EventBridge: ModelApproved
                                                                            │
                                                                     deploy_model Lambda (自动)
                                                                            │
                                                                     SageMaker Endpoint
```

## 功能

- 自动化训练流水线（数据处理 → 训练 → 评估）
- 质量门控（accuracy 阈值，不达标不注册）
- 模型版本管理（S3 + DynamoDB 自建 Registry）
- 人工审批流程
- 审批通过后自动部署（蓝绿切换，零停机）

## 项目结构

```
model-registry/
├── lambda/
│   ├── register_model.py    # 注册模型版本到 DynamoDB
│   ├── approve_model.py     # 审批/拒绝模型，触发 EventBridge
│   └── deploy_model.py      # 接收事件，部署 SageMaker Endpoint
├── pipeline/
│   ├── pipeline_definition.py  # SageMaker Pipeline 定义（一次性提交）
│   ├── preprocess.py           # 数据处理脚本
│   ├── train.py                # 模型训练脚本
│   └── evaluate.py             # 模型评估脚本
├── inference/
│   ├── Dockerfile           # 推理容器镜像
│   ├── inference.py         # Flask 推理服务（/ping + /invocations）
│   └── serve                # SageMaker 启动脚本
├── cdk/
│   ├── app.py
│   └── model_registry_stack.py  # CDK 基础设施定义（可选）
├── WEBCONSOLE_GUIDE.md      # Web 控制台部署步骤（推荐）
├── EXECUTION_GUIDE.md       # CLI 部署步骤
└── requirements.txt
```

## 快速开始

参考 [WEBCONSOLE_GUIDE.md](./WEBCONSOLE_GUIDE.md) 通过 AWS 控制台完成部署。

主要步骤：
1. 创建 IAM 角色（SageMaker 执行角色 + Lambda 执行角色）
2. 创建 S3、DynamoDB、EventBridge 基础资源
3. 部署三个 Lambda 函数（register / approve / deploy）
4. 构建推理镜像并推送到 ECR
5. 在 SageMaker Studio 提交 Pipeline 定义并触发执行
6. Pipeline 完成后在 DynamoDB 查询待审批模型，执行审批
7. 审批通过后自动部署 Endpoint

## 前置条件

- AWS 中国区账号（北京 cn-north-1 或宁夏 cn-northwest-1）
- 本地安装 Docker（构建推理镜像）
- Python 3.10+

## 中国区适配说明

| 功能 | 全球区 | 中国区方案 |
|------|--------|-----------|
| SageMaker Model Registry | 原生支持 | S3 + DynamoDB 自建 |
| SageMaker Studio | 新版可用 | 使用 JupyterLab Space |
| 推理镜像基础镜像 | SageMaker ECR | `public.ecr.aws/docker/library/python:3.10-slim` |
| Serverless Inference | 支持 | 不支持，使用标准 Endpoint |
