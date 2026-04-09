# MLOps on AWS China — 架构图

## 完整架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        链路一：训练 → 部署                               │
│                                                                         │
│  ┌──────────┐    ┌─────────────────────────────────────────────────┐    │
│  │  S3      │    │          SageMaker Pipeline                     │    │
│  │ raw-data/│───▶│                                                 │    │
│  └──────────┘    │  DataProcessing ──▶ ModelTraining ──▶ ModelEval │    │
│       ▲          │                                          │      │    │
│       │          │                                   CheckAccuracy  │    │
│       │          │                                     │       │   │    │
│       │          │                                  达标    未达标  │    │
│       │          │                                     │       │   │    │
│       │          │                              RegisterModel  结束 │    │
│       │          └─────────────────────────────────┬───────────────┘    │
│       │                                            │                    │
│       │                                            ▼                    │
│       │                                    ┌──────────────┐             │
│       │                                    │   DynamoDB    │             │
│       │                                    │ ModelRegistry │             │
│       │                                    │(PendingApproval)            │
│       │                                    └──────┬───────┘             │
│       │                                           │                     │
│       │                                    人工审批 (Lambda)             │
│       │                                           │                     │
│       │                                           ▼                     │
│       │                                    ┌──────────────┐             │
│       │                                    │  EventBridge  │             │
│       │                                    │ ModelApproved │             │
│       │                                    └──────┬───────┘             │
│       │                                           │                     │
│       │                                           ▼                     │
│       │                                    deploy_model Lambda          │
│       │                                           │                     │
│       │                                           ▼                     │
│       │          ┌────────────────────────────────────────────┐         │
│       │          │         SageMaker Endpoint                 │         │
│       │          │    ┌─────────────┐  ┌──────────────┐      │         │
│       │          │    │   推理服务   │  │ Data Capture  │      │         │
│       │          │    │ /invocations│  │ (自动记录)    │      │         │
│       │          │    └──────┬──────┘  └──────┬───────┘      │         │
│       │          └───────────┼────────────────┼──────────────┘         │
│       │                      │                │                         │
│       │                      ▼                ▼                         │
│       │               返回预测结果     S3 data-capture/                 │
│       │                                       │                         │
│       │                                       │                         │
│  ┌────┴──────────────────────────────────────┐│                         │
│  │          链路二：重训触发                    ││                         │
│  │                                            ││                         │
│  │  触发方式 A：新数据上传                     ││                         │
│  │  ┌──────────┐                              ││                         │
│  │  │ 新 CSV   │  S3 Event                    ││                         │
│  │  │ 上传到   │──────────▶ EventBridge Rule  ││                         │
│  │  │ raw-data/│           │                  ││                         │
│  │  └──────────┘           │                  ││                         │
│  │                         ▼                  ││                         │
│  │              StartPipelineExecution ────────┘│                         │
│  │                                             │                         │
│  │  触发方式 B：预测分布偏移                    │                         │
│  │  EventBridge Scheduler (每天)               │                         │
│  │           │                                 │                         │
│  │           ▼                                 │                         │
│  │    监控 Lambda ◀────── S3 data-capture/ ◀───┘                         │
│  │           │                                                           │
│  │    统计预测分布                                                        │
│  │    对比基准分布                                                        │
│  │           │                                                           │
│  │     偏移超阈值 ──▶ StartPipelineExecution ──▶ 回到链路一               │
│  │     正常 ──▶ 不操作                                                   │
│  └───────────────────────────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────────────────────────┘
```

## 涉及的 AWS 服务

```
┌─────────────────────────────────────────────────────────┐
│                    AWS 中国区 (cn-northwest-1)           │
│                                                         │
│  ┌─────────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ SageMaker   │  │   S3     │  │    DynamoDB       │  │
│  │ ・Pipeline  │  │ ・训练数据│  │ ・ModelRegistry   │  │
│  │ ・Endpoint  │  │ ・模型文件│  │ ・模型版本/指标   │  │
│  │ ・Studio    │  │ ・推理记录│  │ ・部署状态        │  │
│  └─────────────┘  └──────────┘  └───────────────────┘  │
│                                                         │
│  ┌─────────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │  Lambda     │  │EventBridge│  │      ECR          │  │
│  │ ・register  │  │ ・事件总线│  │ ・推理镜像        │  │
│  │ ・approve   │  │ ・规则    │  │                   │  │
│  │ ・deploy    │  │ ・Scheduler│ │                   │  │
│  │ ・monitor   │  │ ・S3 Event│  │                   │  │
│  │ ・alarm     │  │           │  │                   │  │
│  └─────────────┘  └──────────┘  └───────────────────┘  │
│                                                         │
│  ┌─────────────┐  ┌──────────┐                          │
│  │ API Gateway │  │CloudWatch│                          │
│  │ ・推理入口  │  │ ・基础监控│                          │
│  │  (可选)     │  │  (可选)  │                          │
│  └─────────────┘  └──────────┘                          │
└─────────────────────────────────────────────────────────┘
```

## IAM 角色与权限

```
┌─────────────────────────────────────────────────────────────────┐
│                        IAM 角色                                  │
│                                                                  │
│  SageMakerExecutionRole                                          │
│  ├── 信任: sagemaker.amazonaws.com                               │
│  ├── AmazonSageMakerFullAccess                                   │
│  └── 内联: SageMakerInvokeLambda (调用 model-registry-* Lambda) │
│                                                                  │
│  LambdaMLOpsRole                                                 │
│  ├── 信任: lambda.amazonaws.com                                  │
│  ├── AmazonDynamoDBFullAccess                                    │
│  ├── AmazonEventBridgeFullAccess                                 │
│  ├── AmazonSageMakerFullAccess                                   │
│  ├── 内联: LambdaMLOpsPolicy (iam:PassRole + StartPipeline)     │
│  └── 内联: LambdaS3ReadAccess (读取 Data Capture 数据)          │
│                                                                  │
│  EventBridgeSchedulerRole                                        │
│  ├── 信任: scheduler.amazonaws.com (不是 events.amazonaws.com)   │
│  └── 内联: EventBridgeSchedulerInvokeLambda                      │
│                                                                  │
│  Lambda 资源策略（自动添加）                                      │
│  ├── model-registry-deploy: 允许 events.amazonaws.com 调用       │
│  └── model-registry-alarm-retrain: 允许 events.amazonaws.com 调用│
└─────────────────────────────────────────────────────────────────┘
```

## 关键注意事项

1. **EventBridge Scheduler vs EventBridge Rules 使用不同的服务主体**
   - Scheduler: `scheduler.amazonaws.com`
   - Rules: `events.amazonaws.com`

2. **EventBridge Rules 的 Lambda 目标不需要执行角色（RoleArn）**
   - Web Console 创建规则时可能自动添加 RoleArn，需要删除
   - Lambda 通过资源策略（Resource-based Policy）授权 EventBridge 调用

3. **监控 Lambda 需要 S3 读取权限**
   - `LambdaMLOpsRole` 需要额外的 S3 GetObject/ListBucket 权限

## 数据流向

```
训练数据 (CSV)
    │
    ▼
S3 raw-data/ ──────────────────────────────────────┐
    │                                               │
    ▼                                               │
Pipeline: DataProcessing                            │
    │ train/ + validation/                          │
    ▼                                               │
Pipeline: ModelTraining                             │
    │ model.tar.gz                                  │
    ▼                                               │
S3 model-artifacts/ ──────┐                         │
    │                     │                         │
    ▼                     │                         │
Pipeline: ModelEvaluation │                         │
    │ evaluation.json     │                         │
    ▼                     │                         │
DynamoDB (metrics)        │                         │
                          │                         │
                          ▼                         │
                   SageMaker Endpoint               │
                          │                         │
                          ▼                         │
                   S3 data-capture/ ──▶ 监控 Lambda │
                                           │        │
                                      偏移检测      │
                                           │        │
                                           ▼        │
                                    重触发 Pipeline ─┘
```
