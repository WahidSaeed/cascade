# SageMaker training image (CASCADE_DL_TRAINING_IMAGE): the backend image with torch (WITH_DL=1) and the base model
# baked in, plus the `train` command SageMaker runs. It serves both the agent's dl fine-tunes and user-edited
# ml / dl code from the workspace editor (hyperparameter mode=source; those jobs run with network isolation).
#   docker build -f backend/Dockerfile --build-arg WITH_DL=1 -t cascade-api:dl .
#   docker build -f infra/runtime-images/training.Dockerfile --build-arg BASE=cascade-api:dl -t cascade-training .
ARG BASE=cascade-api:dl
FROM ${BASE}
RUN printf '#!/bin/sh\nexec python -m app.compilers.sagemaker_entry "$@"\n' > /usr/local/bin/train \
    && chmod +x /usr/local/bin/train
