FROM public.ecr.aws/docker/library/python:3.10-slim

RUN pip install --no-cache-dir flask gunicorn

COPY inference.py /opt/ml/code/inference.py

WORKDIR /opt/ml/code

# SageMaker 用 'serve' 命令启动推理容器
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve
