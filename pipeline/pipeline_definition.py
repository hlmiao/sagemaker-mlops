"""
pipeline_definition.py
完整 SageMaker Pipeline 定义
步骤: DataProcessing -> ModelTraining -> ModelEvaluation -> (条件) -> RegisterModel
"""
import sagemaker
from sagemaker.workflow.pipeline import Pipeline
from sagemaker.workflow.steps import ProcessingStep, TrainingStep
from sagemaker.workflow.pipeline_context import PipelineSession
from sagemaker.workflow.lambda_step import LambdaStep
from sagemaker.workflow.conditions import ConditionGreaterThanOrEqualTo
from sagemaker.workflow.condition_step import ConditionStep
from sagemaker.workflow.functions import JsonGet
from sagemaker.workflow.properties import PropertyFile
from sagemaker.workflow.parameters import ParameterFloat, ParameterString
from sagemaker.sklearn.processing import SKLearnProcessor
from sagemaker.processing import ProcessingInput, ProcessingOutput
from sagemaker.sklearn.estimator import SKLearn
from sagemaker.inputs import TrainingInput
from sagemaker.lambda_helper import Lambda
from sagemaker import get_execution_role

# ── 替换这两个值 ──────────────────────────────────────
ACCOUNT_ID = "YOUR_ACCOUNT_ID"
BUCKET     = "YOUR_BUCKET"
REGION     = "cn-northwest-1"
# ─────────────────────────────────────────────────────

role = get_execution_role()
pipeline_session = PipelineSession()

# Pipeline 参数
accuracy_threshold = ParameterFloat(name="AccuracyThreshold", default_value=0.8)
model_name_param   = ParameterString(name="ModelName", default_value="fraud-detection")
retrain_count      = ParameterString(name="RetrainCount", default_value="0")

# ── Step 1: 数据处理 ──────────────────────────────────
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
    inputs=[
        ProcessingInput(
            source=f"s3://{BUCKET}/raw-data/",
            destination="/opt/ml/processing/input",
        )
    ],
    outputs=[
        ProcessingOutput(output_name="train", source="/opt/ml/processing/output/train"),
        ProcessingOutput(output_name="validation", source="/opt/ml/processing/output/validation"),
    ],
    code="preprocess.py",
)

# ── Step 2: 模型训练 ──────────────────────────────────
estimator = SKLearn(
    entry_point="train.py",
    framework_version="1.2-1",
    role=role,
    instance_count=1,
    instance_type="ml.m5.xlarge",
    output_path=f"s3://{BUCKET}/model-artifacts/",
    sagemaker_session=pipeline_session,
    hyperparameters={"n-estimators": 100, "max-depth": 5},
)
step_train = TrainingStep(
    name="ModelTraining",
    estimator=estimator,
    inputs={
        "train": TrainingInput(
            s3_data=step_process.properties.ProcessingOutputConfig
                    .Outputs["train"].S3Output.S3Uri
        )
    },
)

# ── Step 3: 模型评估 ──────────────────────────────────
eval_processor = SKLearnProcessor(
    framework_version="1.2-1",
    role=role,
    instance_type="ml.m5.xlarge",
    instance_count=1,
    sagemaker_session=pipeline_session,
)
evaluation_report = PropertyFile(
    name="EvaluationReport",
    output_name="evaluation",
    path="evaluation.json",
)
step_evaluate = ProcessingStep(
    name="ModelEvaluation",
    processor=eval_processor,
    inputs=[
        ProcessingInput(
            source=step_train.properties.ModelArtifacts.S3ModelArtifacts,
            destination="/opt/ml/processing/model",
        ),
        ProcessingInput(
            source=step_process.properties.ProcessingOutputConfig
                   .Outputs["validation"].S3Output.S3Uri,
            destination="/opt/ml/processing/validation",
        ),
    ],
    outputs=[
        ProcessingOutput(output_name="evaluation", source="/opt/ml/processing/evaluation"),
    ],
    code="evaluate.py",
    property_files=[evaluation_report],
)

# ── Step 4: 注册模型（条件通过后执行）────────────────
register_lambda = Lambda(
    function_arn=f"arn:aws-cn:lambda:{REGION}:{ACCOUNT_ID}:function:model-registry-register"
)
step_register = LambdaStep(
    name="RegisterModel",
    lambda_func=register_lambda,
    inputs={
        "model_name": model_name_param,
        "s3_model_uri": step_train.properties.ModelArtifacts.S3ModelArtifacts,
        "pipeline_run_id": sagemaker.workflow.execution_variables.ExecutionVariables.PIPELINE_EXECUTION_ID,
        "accuracy": JsonGet(
            step_name=step_evaluate.name,
            property_file=evaluation_report,
            json_path="metrics[0].value",
        ),
    },
)

# ── Step 4b: 自动重训（评估未达标时触发）────────────
retrain_lambda = Lambda(
    function_arn=f"arn:aws-cn:lambda:{REGION}:{ACCOUNT_ID}:function:model-registry-retrain"
)
step_retrain = LambdaStep(
    name="RetrainTrigger",
    lambda_func=retrain_lambda,
    inputs={
        "model_name": model_name_param,
        "retrain_count": retrain_count,
        "accuracy": JsonGet(
            step_name=step_evaluate.name,
            property_file=evaluation_report,
            json_path="metrics[0].value",
        ),
    },
)

# ── Step 5: 条件判断（accuracy >= threshold 才注册）──
condition = ConditionGreaterThanOrEqualTo(
    left=JsonGet(
        step_name=step_evaluate.name,
        property_file=evaluation_report,
        json_path="metrics[0].value",
    ),
    right=accuracy_threshold,
)
step_condition = ConditionStep(
    name="CheckAccuracy",
    conditions=[condition],
    if_steps=[step_register],
    else_steps=[],   # 未达标直接结束，不注册，等待新数据后手动重触发
)

# ── 组装 Pipeline ─────────────────────────────────────
pipeline = Pipeline(
    name="mlops-fraud-detection",
    parameters=[accuracy_threshold, model_name_param, retrain_count],
    steps=[step_process, step_train, step_evaluate, step_condition],
    sagemaker_session=pipeline_session,
)

pipeline.upsert(role_arn=role)
print("Pipeline upserted successfully")
print(f"Pipeline ARN: {pipeline.describe()['PipelineArn']}")