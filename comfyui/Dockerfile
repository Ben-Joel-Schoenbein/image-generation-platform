# ComfyUI is the local inference engine. The model weights are mounted from ./models.
ARG PYTORCH_BASE=pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime
FROM ${PYTORCH_BASE}
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/ComfyUI
# QWEN_21_QUALITY_UPGRADE_V1
RUN git init /opt/ComfyUI \
    && git remote add origin https://github.com/Comfy-Org/ComfyUI.git \
    && git fetch --depth 1 origin f1072eb0350638a3390ddb6afbcaa8c6b237c6fd \
    && git checkout --detach FETCH_HEAD \
    && grep -Ev '^(torch|torchvision|torchaudio)([<=> ].*)?$' requirements.txt > /tmp/comfy-requirements.txt \
    && pip install --no-cache-dir -r /tmp/comfy-requirements.txt
COPY custom_nodes/studio_qwen_references /opt/ComfyUI/custom_nodes/studio_qwen_references
COPY custom_nodes/studio_qwen21_quality /opt/ComfyUI/custom_nodes/studio_qwen21_quality
COPY custom_nodes/studio_rapid_aio /opt/ComfyUI/custom_nodes/studio_rapid_aio
EXPOSE 8188
