# YusufClaw web UI + AG-UI gateway as a Lambda container (Lambda Web Adapter, response streaming).
FROM public.ecr.aws/docker/library/python:3.12-slim
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AWS_LWA_PORT=8080 AWS_LWA_INVOKE_MODE=response_stream \
    AWS_LWA_READINESS_CHECK_PATH=/health AWS_LWA_ASYNC_INIT=true \
    WEB_HOST=0.0.0.0 WEB_PORT=8080 MEMORY_PATH=/tmp/memory.sqlite
WORKDIR /app
COPY pyproject.toml PYTHON-SCAFFOLD.md ./
COPY src ./src
RUN pip install --no-cache-dir . && rm -rf /root/.cache
COPY skills ./skills
COPY roles ./roles
COPY infra_py/web_lambda/run_web.py ./run_web.py
CMD ["python", "/app/run_web.py"]
