# Qwen-Image-2.1 生图推理服务（独立容器，不随 agent compose 启动）
#
# 构建:  cd deploy/image-gen && docker compose build
# 模型权重不打进镜像，运行时挂载宿主机 ~/qwen-image-2.1/models
#
# 两阶段构建：devel 镜像编译 stable-diffusion.cpp（CUDA），runtime 镜像只带二进制 + Python API。

ARG CUDA_VERSION=12.4.1
ARG UBUNTU=ubuntu22.04

# ---------- 阶段 1：编译 sd-cli（CUDA） ----------
FROM nvidia/cuda:${CUDA_VERSION}-devel-${UBUNTU} AS builder

ARG SD_CPP_REPO=https://github.com/leejet/stable-diffusion.cpp.git
# 固定到与 WSL 本机部署一致的 commit，保证行为可复现
ARG SD_CPP_COMMIT=3f8527a46c54ecf4cb4ed6003da8e8982283c73c
# 计算能力：RTX 4090=89，30 系=86，50 系=120
ARG CUDA_ARCH=89

# apt 换国内源（直连，不走代理；compose 的 NO_PROXY 已含 mirrors.aliyun.com）
RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git cmake build-essential \
    && rm -rf /var/lib/apt/lists/*

# ggml 是 git submodule，必须 --recurse-submodules / submodule update 才有 CMakeLists
RUN git clone --depth 1 ${SD_CPP_REPO} /src \
    && cd /src \
    && git fetch --depth 1 origin ${SD_CPP_COMMIT} \
    && git checkout ${SD_CPP_COMMIT} \
    && git submodule update --init --recursive

RUN cd /src \
    && cmake -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
        -DSD_BUILD_TESTS=OFF \
    && cmake --build build --target sd-cli -j"$(nproc)"

# ---------- 阶段 2：运行时 ----------
FROM nvidia/cuda:${CUDA_VERSION}-runtime-${UBUNTU}

RUN sed -i 's|http://archive.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|http://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip libgomp1 libcurl4 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY server.py .
COPY --from=builder /src/build/bin/sd-cli /usr/local/bin/sd-cli

# /models  : 模型权重（compose 挂载，只读）
# /outputs : 生成图留档（compose 挂载）
VOLUME ["/models", "/outputs"]

EXPOSE 8000
CMD ["python3", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
