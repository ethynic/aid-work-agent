# image-gen：Qwen-Image-2.1 生图推理容器（独立部署）

给 agent 提供文生图/图生图能力的本地推理服务。**独立于 agent 主体**：本目录的 compose 与 agent 的 compose 互不引用、不一起启停，完全手动管理（agent 的重启/更新不影响本服务；本服务崩溃也不影响 agent）。

| 项 | 说明 |
|---|---|
| 推理内核 | [leejet/stable-diffusion.cpp](https://github.com/leejet/stable-diffusion.cpp)（固定 commit `3f8527a`，与 WSL 手动部署同版）+ GGUF 量化 Qwen-Image-2.1 |
| 文本编码器 | Qwen3-VL-8B-Instruct Q4_K_XL（+ mmproj，编辑模式用视觉输入） |
| 硬件要求 | NVIDIA GPU，`--backend cuda0` 全权重上 GPU 约占 13~15G 显存（Q8_0）；当前机器 RTX 4090 24G 满足 |
| 模型权重 | **不打进镜像**，挂载宿主机 `~/qwen-image-2.1/models`（Q8_0 7.2G + Q4_K_M 4.0G + 编码器 4.8G + VAE 0.6G） |
| 端口 | 宿主机 `127.0.0.1:8290` → 容器 8000（仅本机；Windows 侧 `http://localhost:8290`） |
| 并发 | sd-cli 单任务即占满 GPU，服务端全局锁**串行**执行，请求自动排队 |

## 构建与启动（手动）

```bash
# WSL 内执行；构建含 CUDA 编译，首次约 5~20 分钟
cd /mnt/c/repos/aid-work-agent/deploy/image-gen
docker compose build

# 【一次性·root】生成 NVIDIA CDI 规范（WSL2 GPU 必需，详见下方"WSL2 GPU 坑"）
wsl -d Ubuntu-22.04 -u root -- nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml

# 启动（不随 agent、不随 docker 自启；restart: "no"）
docker compose up -d

# 健康检查（devices 应列出 CUDA0）
curl -s http://127.0.0.1:8290/health | python3 -m json.tool

# 停止 / 日志
docker compose down
docker compose logs -f
```

### WSL2 GPU 坑（为什么用 CDI 而不是 --gpus all）

本机（WSL2 Ubuntu-22.04 + RTX 4090 + nvidia-container-toolkit 1.18）用 legacy 模式（`--gpus all` / `driver: nvidia`）时，容器内 cuInit 报 `CUDA driver version is insufficient`：toolkit 不挂载 `/usr/lib/wsl/drivers/` 下的 `libnvdxgdmal.so.1` 与 `nvcubins.bin`，WSL 版 libcuda 薄层缺它们无法初始化。CDI 规范会完整挂载这些文件，所以 compose 使用 `devices: [nvidia.com/gpu=all]`。Windows 驱动大版本更新后需重新生成 CDI 规范。

不使用 compose 的等效手动方式：

```bash
docker run -d --name image-gen --gpus all -p 127.0.0.1:8290:8000 \
  -v /home/ethynic/qwen-image-2.1/models:/models:ro \
  -v /home/ethynic/qwen-image-2.1/outputs:/outputs \
  image-gen:latest
```

## API

响应格式对齐 OpenAI images API（`data[0].b64_json`），同时留档 PNG 到挂载的 `outputs/` 目录。

### 文生图 `POST /v1/images/generations`

```bash
curl -s http://127.0.0.1:8290/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "一只戴安全帽的橘猫工程师在工地看图纸", "width": 1024, "height": 1024, "steps": 20}' \
  | python3 -c 'import sys,json,base64; d=json.load(sys.stdin); open("out.png","wb").write(base64.b64decode(d["data"][0]["b64_json"])); print(d["meta"])'
```

字段：`prompt`（必填）、`width/height`（默认 1024，自动对齐到 32 的倍数）、`steps`（默认 20）、`cfg_scale`（默认 6.0）、`negative_prompt`、`quant`（`Q8_0` 画质 / `Q4_K_M` 速度，默认取环境变量）。

### 图生图/编辑 `POST /v1/images/edits`

```bash
IMG_B64=$(base64 -w0 输入图.png)
curl -s http://127.0.0.1:8290/v1/images/edits \
  -H 'Content-Type: application/json' \
  -d "{\"prompt\": \"把背景换成雪山\", \"image\": \"$IMG_B64\", \"strength\": 0.75}"
```

字段：`prompt`、`image`（**PNG** base64，可带 `data:image/png;base64,` 前缀）必填；`strength` 重绘幅度默认 0.75；`width/height` 默认与输入图一致；其余同上。

## agent 侧如何接入（可选，不强制绑定）

agent 容器（`aid-ai-service` 等）默认与本服务不同网络。两种方式任选：

1. **跨网络直连（推荐，不改 agent 配置）**：把本容器挂进 agent 的网络
   ```bash
   docker network connect deploy_aid-network image-gen
   # agent 容器内即可访问 http://image-gen:8000
   ```
2. 端口已映射宿主机 `127.0.0.1:8290`，WSL/Windows 侧直接访问。

agent 代码侧的 provider/工具接入属后续任务（见 `docs/research/image-generation-model-research.md` 的接入建议），本目录只负责独立推理服务。

## 重建与迁移

保留的 Dockerfile 即重建依据（模型在宿主机，不参与构建）：

```bash
docker compose build --no-cache   # 拉取固定 commit 源码 + CUDA 编译
```

- 换机器：新机装 NVIDIA 驱动 + docker nvidia runtime，拷贝本目录与 `~/qwen-image-2.1/models`，按 compose 里的卷路径（或 `IMAGE_MODELS_DIR` 环境变量）挂载即可。
- 换显卡：改 compose 的 `CUDA_ARCH` 构建参数（4090=89 / 30 系=86 / 50 系=120）后重建。
- 升级 stable-diffusion.cpp：改 Dockerfile 的 `SD_CPP_COMMIT` 后重建（建议先在 WSL 手动部署验证再固定 commit）。
- 显存不足：`IMAGE_GEN_QUANT=Q4_K_M docker compose up -d`，或 `SD_BACKEND=cpu` 回退 CPU（慢）。

### 本机已知网络坑：docker daemon 无代理

本机 docker daemon 未配代理，registry-mirrors（docker.1ms.run 等）对 `nvidia/cuda` 大镜像经常 TLS 超时，导致 `docker compose build` 拉基础镜像失败。**不要为此重启 docker daemon**（会连带重启 aid-\* 业务容器）。绕行方法：用 crane 静态二进制走 shell 代理（WSL 内 `127.0.0.1:7897`）拉取后 `docker load`：

```bash
cd /tmp
curl -sL -o crane.tar.gz https://github.com/google/go-containerregistry/releases/download/v0.20.2/go-containerregistry_Linux_x86_64.tar.gz
tar xzf crane.tar.gz crane
./crane pull --format=tarball nvidia/cuda:12.4.1-devel-ubuntu22.04  /tmp/cuda-devel.tar   # 4G，断流就重跑
./crane pull --format=tarball nvidia/cuda:12.4.1-runtime-ubuntu22.04 /tmp/cuda-runtime.tar
docker load -i /tmp/cuda-devel.tar && docker load -i /tmp/cuda-runtime.tar
```

镜像入本地库后，`docker compose build` 默认不 `--pull`，直接用本地基础镜像；构建期的 git clone/pip 走 compose 里的 `network: host` + 代理 build-args（`IMAGE_BUILD_PROXY` 变量，默认 `http://127.0.0.1:7897`），换环境时按需覆盖。

## 文件清单

| 文件 | 用途 |
|---|---|
| `Dockerfile` | 两阶段构建：devel 编译 sd-cli（CUDA）→ runtime 只带二进制 + FastAPI |
| `server.py` | API 包装：串行锁、超时保护、b64 返回 + outputs 留档 |
| `docker-compose.yml` | 独立 compose（GPU、模型挂载、127.0.0.1 端口） |
