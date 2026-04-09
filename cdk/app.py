#!/usr/bin/env python3
import aws_cdk as cdk
from model_registry_stack import ModelRegistryStack

app = cdk.App()

ModelRegistryStack(
    app, "ModelRegistryStack",
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region"),  # cn-north-1 or cn-northwest-1
    ),
)

app.synth()
