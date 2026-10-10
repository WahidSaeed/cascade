# Lambda container image for `dl` artifacts: onnxruntime + tokenizers + the shared artifact runner.
FROM public.ecr.aws/lambda/python:3.12
RUN pip install --no-cache-dir "onnxruntime==1.20.1" "tokenizers==0.21.0" "numpy<3"
COPY backend/app/artifact_runner.py ${LAMBDA_TASK_ROOT}/lambda_function.py
CMD ["lambda_function.lambda_handler"]
