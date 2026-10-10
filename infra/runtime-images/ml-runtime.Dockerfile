# Lambda container image for `ml` artifacts: onnxruntime + the shared artifact runner.
# Each function points MODEL_URI at its artifact's S3 prefix; the model loads once per cold start.
FROM public.ecr.aws/lambda/python:3.12
RUN pip install --no-cache-dir "onnxruntime==1.20.1" "numpy<3"
COPY backend/app/artifact_runner.py ${LAMBDA_TASK_ROOT}/lambda_function.py
CMD ["lambda_function.lambda_handler"]
