"""
model_registry_stack.py
CDK Stack: 自建 Model Registry 替代 SageMaker Model Registry
适配 AWS 中国区 (cn-north-1 / cn-northwest-1)
"""
from aws_cdk import (
    Stack, RemovalPolicy, Duration,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_apigateway as apigw,
)
from constructs import Construct


class ModelRegistryStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        # ── 1. S3: 存储模型 artifact ──────────────────────────────────────
        model_bucket = s3.Bucket(
            self, "ModelArtifactBucket",
            bucket_name=f"ml-model-artifacts-{self.account}-{self.region}",
            versioned=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            removal_policy=RemovalPolicy.RETAIN,  # 生产环境保留
        )

        # ── 2. DynamoDB: 模型元数据注册表 ─────────────────────────────────
        # PK: model_name, SK: version_id
        registry_table = dynamodb.Table(
            self, "ModelRegistryTable",
            table_name="ModelRegistry",
            partition_key=dynamodb.Attribute(name="model_name", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="version_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
        )
        # GSI: 按状态查询 (PendingApproval / Approved / Rejected)
        registry_table.add_global_secondary_index(
            index_name="StatusIndex",
            partition_key=dynamodb.Attribute(name="status", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="created_at", type=dynamodb.AttributeType.STRING),
        )

        # ── 3. EventBridge: 自定义事件总线 ───────────────────────────────
        event_bus = events.EventBus(self, "ModelRegistryBus", event_bus_name="model-registry-bus")

        # ── 4. SageMaker 执行角色 (供 deploy Lambda 使用) ─────────────────
        sagemaker_role = iam.Role(
            self, "SageMakerExecutionRole",
            assumed_by=iam.ServicePrincipal("sagemaker.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSageMakerFullAccess"),
            ],
        )
        model_bucket.grant_read(sagemaker_role)

        # ── 5. Lambda 公共环境变量 & 基础权限 ────────────────────────────
        common_env = {
            "MODEL_REGISTRY_TABLE": registry_table.table_name,
            "EVENT_BUS_NAME": event_bus.event_bus_name,
        }

        # ── 6. Lambda: register_model ─────────────────────────────────────
        register_fn = lambda_.Function(
            self, "RegisterModelFn",
            function_name="model-registry-register",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="register_model.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            environment=common_env,
            timeout=Duration.seconds(30),
        )
        registry_table.grant_write_data(register_fn)

        # ── 7. Lambda: approve_model ──────────────────────────────────────
        approve_fn = lambda_.Function(
            self, "ApproveModelFn",
            function_name="model-registry-approve",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="approve_model.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            environment=common_env,
            timeout=Duration.seconds(30),
        )
        registry_table.grant_read_write_data(approve_fn)
        event_bus.grant_put_events_to(approve_fn)

        # ── 8. Lambda: deploy_model ───────────────────────────────────────
        deploy_fn = lambda_.Function(
            self, "DeployModelFn",
            function_name="model-registry-deploy",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="deploy_model.lambda_handler",
            code=lambda_.Code.from_asset("../lambda"),
            environment={
                **common_env,
                "SAGEMAKER_EXECUTION_ROLE": sagemaker_role.role_arn,
                "SAGEMAKER_ENDPOINT_NAME": "ml-model-endpoint",
                "ENDPOINT_INSTANCE_TYPE": "ml.m5.large",
                "ENDPOINT_INSTANCE_COUNT": "1",
                "DEFAULT_INFERENCE_IMAGE": f"727897471807.dkr.ecr.{self.region}.amazonaws.com.cn/sagemaker-scikit-learn:1.2-1-cpu-py3",
            },
            timeout=Duration.seconds(60),
        )
        registry_table.grant_read_write_data(deploy_fn)
        # 允许 deploy Lambda 操作 SageMaker
        deploy_fn.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "sagemaker:CreateModel",
                "sagemaker:CreateEndpointConfig",
                "sagemaker:CreateEndpoint",
                "sagemaker:UpdateEndpoint",
                "sagemaker:DescribeEndpoint",
            ],
            resources=["*"],
        ))
        deploy_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=[sagemaker_role.role_arn],
        ))

        # ── 9. EventBridge Rule: ModelApproved -> deploy_model ───────────
        events.Rule(
            self, "ModelApprovedRule",
            event_bus=event_bus,
            rule_name="model-approved-trigger-deploy",
            description="Trigger deployment when a model is approved",
            event_pattern=events.EventPattern(
                source=["custom.model-registry"],
                detail_type=["ModelApproved"],
            ),
            targets=[targets.LambdaFunction(deploy_fn)],
        )

        # ── 10. API Gateway: 审批接口 (人工审批入口) ──────────────────────
        api = apigw.RestApi(
            self, "ModelRegistryApi",
            rest_api_name="model-registry-api",
            description="Model Registry approval API",
            deploy_options=apigw.StageOptions(stage_name="prod"),
        )
        models_resource = api.root.add_resource("models")
        approve_resource = models_resource.add_resource("{model_name}") \
            .add_resource("versions").add_resource("{version_id}").add_resource("approval")

        approve_resource.add_method(
            "POST",
            apigw.LambdaIntegration(approve_fn),
        )
        # 注册接口（供 SageMaker Pipeline 调用）
        register_resource = models_resource.add_resource("register")
        register_resource.add_method(
            "POST",
            apigw.LambdaIntegration(register_fn),
        )
