"""Qwen-Image-2.1 生图推理 API（stable-diffusion.cpp GPU 版包装）。

独立于 agent 主体部署：手动 `docker compose up -d` 启停，不随 agent compose 拉起。
sd-cli 单次推理即占满 GPU，因此全局锁串行执行；输出留档 /outputs 并以 b64_json 返回。

端点（响应格式对齐 OpenAI images API，便于 agent 侧复用现有对接习惯）：
  GET  /health
  POST /v1/images/generations   文生图
  POST /v1/images/edits         图生图/编辑（输入 PNG base64 + 指令）
"""

import asyncio
import base64
import logging
import os
import re
import struct
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("image-gen")

SD_CLI = os.environ.get("SD_CLI", "sd-cli")
MODELS = os.environ.get("MODELS_DIR", "/models")
OUTPUTS = os.environ.get("OUTPUTS_DIR", "/outputs")
DEFAULT_QUANT = os.environ.get("QUANT", "Q8_0")
SD_BACKEND = os.environ.get("SD_BACKEND", "cuda0")
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT_SECONDS", "900"))
ALLOWED_QUANTS = ("Q8_0", "Q4_K_M")

DIFFUSION = os.path.join(MODELS, "diffusion", "qwen-image-2.1-{quant}.gguf")
VAE = os.path.join(MODELS, "vae", "qwen_image_2.1_vae_bf16.safetensors")
LLM = os.path.join(MODELS, "text_encoder", "Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf")
LLM_VISION = os.path.join(MODELS, "text_encoder", "mmproj-BF16.gguf")

app = FastAPI(title="image-gen", description="Qwen-Image-2.1 t2i/edit via stable-diffusion.cpp")
_job_lock = asyncio.Lock()
_devices_cache: list[str] | None = None


def _align32(v: int) -> int:
    return max(32, (v // 32) * 32)


def _model_paths(quant: str) -> dict[str, str]:
    paths = {
        "diffusion": DIFFUSION.format(quant=quant),
        "vae": VAE,
        "llm": LLM,
        "llm_vision": LLM_VISION,
    }
    for name, p in paths.items():
        if not os.path.exists(p):
            raise HTTPException(status_code=503, detail=f"模型文件缺失: {name} -> {p}")
    return paths


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(status_code=400, detail="输入图必须是 PNG（base64，可带 data:image/png 前缀）")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def _decode_b64_image(image: str) -> bytes:
    payload = re.sub(r"^data:image/[a-zA-Z]+;base64,", "", image.strip())
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="image 不是合法的 base64")


async def _run_sd_cli(args: list[str]) -> str:
    """执行 sd-cli；成功返回 stdout，失败/超时抛 HTTPException。"""
    cmd = [SD_CLI, *args]
    log.info("exec: %s", " ".join(cmd))
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=JOB_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail=f"生成超时（>{JOB_TIMEOUT}s），已终止")
    text = (out or b"").decode(errors="replace")
    elapsed = time.monotonic() - start
    if proc.returncode != 0:
        tail = text[-2000:]
        log.error("sd-cli 失败 rc=%s 耗时 %.1fs\n%s", proc.returncode, elapsed, tail)
        raise HTTPException(status_code=500, detail=f"sd-cli 退出码 {proc.returncode}: {tail}")
    log.info("sd-cli 完成，耗时 %.1fs", elapsed)
    return text


def _read_png_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


class GenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    width: int = Field(default=1024, ge=32, le=4096)
    height: int = Field(default=1024, ge=32, le=4096)
    steps: int = Field(default=20, ge=1, le=100)
    cfg_scale: float = Field(default=6.0, gt=0, le=20)
    negative_prompt: str = ""
    quant: str = DEFAULT_QUANT


class EditRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    image: str = Field(min_length=32, description="PNG base64，可带 data:image/png;base64, 前缀")
    strength: float = Field(default=0.75, gt=0, le=1)
    width: int | None = Field(default=None, ge=32, le=4096, description="默认与输入图一致")
    height: int | None = Field(default=None, ge=32, le=4096)
    steps: int = Field(default=20, ge=1, le=100)
    cfg_scale: float = Field(default=6.0, gt=0, le=20)
    negative_prompt: str = ""
    quant: str = DEFAULT_QUANT


@app.get("/health")
async def health():
    global _devices_cache
    if _devices_cache is None:
        proc = await asyncio.create_subprocess_exec(
            SD_CLI, "--list-devices",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        _devices_cache = [
            line.split("\t")[0] for line in (out or b"").decode(errors="replace").splitlines() if line.strip()
        ]
    return {
        "status": "ok",
        "backend": SD_BACKEND,
        "default_quant": DEFAULT_QUANT,
        "devices": _devices_cache,
        "busy": _job_lock.locked(),
    }


async def _generate(prefix: str, req_args: list[str], quant: str, meta: dict) -> dict:
    paths = _model_paths(quant)
    os.makedirs(OUTPUTS, exist_ok=True)
    out_path = os.path.join(OUTPUTS, f"{prefix}-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png")

    args = [
        "--diffusion-model", paths["diffusion"],
        "--vae", paths["vae"],
        "--llm", paths["llm"],
        "--backend", SD_BACKEND,
        *req_args,
        "--sampling-method", "euler",
        "--fa",
        "-v",
        "-o", out_path,
    ]
    async with _job_lock:
        await _run_sd_cli(args)

    if not os.path.exists(out_path):
        raise HTTPException(status_code=500, detail="sd-cli 成功退出但未生成输出文件")

    meta = {**meta, "quant": quant, "file": os.path.basename(out_path), "backend": SD_BACKEND}
    return {"created": int(time.time()), "data": [{"b64_json": _read_png_b64(out_path)}], "meta": meta}


@app.post("/v1/images/generations")
async def generations(req: GenerationRequest):
    if req.quant not in ALLOWED_QUANTS:
        raise HTTPException(status_code=400, detail=f"quant 仅支持 {ALLOWED_QUANTS}")
    w, h = _align32(req.width), _align32(req.height)
    args = [
        "-p", req.prompt,
        "--steps", str(req.steps),
        "--cfg-scale", str(req.cfg_scale),
        "-W", str(w), "-H", str(h),
    ]
    if req.negative_prompt:
        args += ["-n", req.negative_prompt]
    meta = {"kind": "t2i", "prompt": req.prompt, "width": w, "height": h, "steps": req.steps}
    return await _generate("t2i", args, req.quant, meta)


@app.post("/v1/images/edits")
async def edits(req: EditRequest):
    if req.quant not in ALLOWED_QUANTS:
        raise HTTPException(status_code=400, detail=f"quant 仅支持 {ALLOWED_QUANTS}")
    data = _decode_b64_image(req.image)
    src_w, src_h = _png_size(data)
    w = _align32(req.width or src_w)
    h = _align32(req.height or src_h)

    tmp_in = os.path.join(OUTPUTS, f"edit-in-{uuid.uuid4().hex[:8]}.png")
    os.makedirs(OUTPUTS, exist_ok=True)
    with open(tmp_in, "wb") as f:
        f.write(data)

    paths = _model_paths(req.quant)
    out_path = os.path.join(OUTPUTS, f"edit-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png")
    args = [
        "--diffusion-model", paths["diffusion"],
        "--vae", paths["vae"],
        "--llm", paths["llm"],
        "--llm_vision", paths["llm_vision"],
        "-r", tmp_in,
        "-p", req.prompt,
        "--strength", str(req.strength),
        "--steps", str(req.steps),
        "--cfg-scale", str(req.cfg_scale),
        "-W", str(w), "-H", str(h),
        "--sampling-method", "euler",
        "--fa", "-v", "-o", out_path,
        "--backend", SD_BACKEND,
    ]
    if req.negative_prompt:
        args += ["-n", req.negative_prompt]

    async with _job_lock:
        try:
            await _run_sd_cli(args)
        finally:
            try:
                os.remove(tmp_in)
            except OSError:
                pass

    if not os.path.exists(out_path):
        raise HTTPException(status_code=500, detail="sd-cli 成功退出但未生成输出文件")
    meta = {
        "kind": "edit", "prompt": req.prompt, "strength": req.strength,
        "width": w, "height": h, "steps": req.steps,
        "quant": req.quant, "file": os.path.basename(out_path), "backend": SD_BACKEND,
    }
    return {"created": int(time.time()), "data": [{"b64_json": _read_png_b64(out_path)}], "meta": meta}
