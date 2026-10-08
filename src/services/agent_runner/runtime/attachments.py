from __future__ import annotations
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any, Optional, List, Dict, Tuple
from loguru import logger

class AttachmentAssembler:
    def __init__(self, workspace_root=None):
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
    _IMAGE_EXT_MIME = {"png":"image/png", "jpg":"image/jpeg", "jpeg":"image/jpeg", "webp":"image/webp", "gif":"image/gif"}
    _MULTIMODAL_MAX_IMAGES = 3
    _MULTIMODAL_MAX_BYTES = 5 * 1024 * 1024
    _IMAGE_ARTIFACT_PREFIX = "image_"

    def _read_multimodal_image(self, path: str) -> Optional[Tuple[str, str, bytes]]:
        """Read one bounded image; returns (mime, sha256, data) or None when unusable."""
        ext = Path(path).suffix.lstrip(".").lower()
        mime = self._IMAGE_EXT_MIME.get(ext)
        if not mime:
            logger.warning(f"[SUBAGENT] 不支持的图片格式，跳过: {path}")
            return None
        try:
            data = Path(path).read_bytes()
        except Exception as e:
            logger.warning(f"[SUBAGENT] 读取图片失败，跳过: {path}, err={e}")
            return None
        if len(data) > self._MULTIMODAL_MAX_BYTES:
            logger.warning(
                f"[SUBAGENT] 图片过大（{len(data)} bytes > {self._MULTIMODAL_MAX_BYTES}），跳过: {path}"
            )
            return None
        return mime, hashlib.sha256(data).hexdigest(), data

    def build_image_reference_content(
        self, text: str, image_paths: Optional[List[str]]
    ) -> Tuple[Optional[List[Dict[str, Any]]], List[Dict[str, Any]]]:
        """把图片落入执行 owner workspace，content 只写 runner_image 引用部件。

        - 需要执行 owner 命名空间（workspace_root，即 workspace_directory 结果）；
          无 owner 目录的旧内存路径不适用，调用方回退 _build_multimodal_user_content。
        - 沿用单图 5MB / 3 张 / 扩展名白名单限制；文件名按内容 sha256 幂等落盘。
        - 返回 (parts, artifacts)：parts 为持久 content（text + runner_image 引用），
          artifacts 供登记 state.resources['image_artifacts']（含落盘 path）。
        """
        if not image_paths or self.workspace_root is None:
            return None, []
        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        artifacts: List[Dict[str, Any]] = []
        attached = 0
        for path in image_paths:
            if attached >= self._MULTIMODAL_MAX_IMAGES:
                logger.warning(
                    f"[SUBAGENT] image_paths 超过 {self._MULTIMODAL_MAX_IMAGES} 张上限，忽略后续: {path}"
                )
                break
            image = self._read_multimodal_image(path)
            if image is None:
                continue
            mime, digest, data = image
            ext = Path(path).suffix.lstrip(".").lower()
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            target = self.workspace_root / f"{self._IMAGE_ARTIFACT_PREFIX}{digest}.{ext}"
            try:
                if not (target.exists() and target.stat().st_size == len(data)):
                    target.write_bytes(data)
            except Exception as e:
                logger.warning(f"[SUBAGENT] 图片落入 owner workspace 失败，跳过: {path}, err={e}")
                continue
            name = Path(path).name
            parts.append({"type": "runner_image", "name": name, "mime_type": mime,
                          "size_bytes": len(data), "sha256": digest})
            artifacts.append({"name": name, "mime_type": mime, "size_bytes": len(data),
                              "sha256": digest, "path": str(target)})
            attached += 1
        if attached == 0:
            return None, []
        return parts, artifacts

    def _build_multimodal_user_content(
        self, text: str, image_paths: Optional[List[str]]
    ) -> Optional[List[Dict[str, Any]]]:
        """构造 OpenAI 多模态 user content（text + image_url data:base64）。

        - image_paths 为空或全部无效时返回 None（调用方按纯文本处理）
        - 单张图片超过 _MULTIMODAL_MAX_BYTES 跳过并记 warning
        - 最多 _MULTIMODAL_MAX_IMAGES 张，超出忽略
        - 不支持的扩展名跳过
        - 这是 provider wire 形态：durable 装配点改用 build_image_reference_content
          只存引用，由 ModelAdapter 在调 gateway 前解析回本形态；旧含 data URL 的
          checkpoint 恢复时也按本形态原样透传，不做二次转换。

        返回的 content 格式：
            [
                {"type": "text", "text": <text>},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
            ]
        """
        if not image_paths:
            return None

        parts: List[Dict[str, Any]] = [{"type": "text", "text": text}]
        attached = 0

        for path in image_paths:
            if attached >= self._MULTIMODAL_MAX_IMAGES:
                logger.warning(
                    f"[SUBAGENT] image_paths 超过 {self._MULTIMODAL_MAX_IMAGES} 张上限，忽略后续: {path}"
                )
                break

            ext = Path(path).suffix.lstrip(".").lower()
            mime = self._IMAGE_EXT_MIME.get(ext)
            if not mime:
                logger.warning(f"[SUBAGENT] 不支持的图片格式，跳过: {path}")
                continue

            try:
                data = Path(path).read_bytes()
            except Exception as e:
                logger.warning(f"[SUBAGENT] 读取图片失败，跳过: {path}, err={e}")
                continue

            if len(data) > self._MULTIMODAL_MAX_BYTES:
                logger.warning(
                    f"[SUBAGENT] 图片过大（{len(data)} bytes > {self._MULTIMODAL_MAX_BYTES}），跳过: {path}"
                )
                continue

            import base64
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
            attached += 1

        if attached == 0:
            return None
        return parts


    def prepare(self, user_input, attachments, identity, skill_registry):
        import base64
        import tempfile
        enhanced_input = user_input
        auto_loaded_skill = None
        uploaded_files_info = []
        session_workspace = None
        handed_off = False
        try:
            files_context = ""
            if attachments:
                attachment_info = []
                for att in attachments:
                    att_type = att.get("type", "file")
                    att_name = Path(str(att.get("name", "unknown"))).name
                    att_url = att.get("url", "")
                    att_mime = att.get("mime_type", "")
                    att_content = att.get("content", "")
                
                    attachment_info.append(f"- {att_name} ({att_type}, {att_mime or 'unknown type'})")
                
                    if skill_registry:
                        matched_skill = skill_registry.match_by_file(att_name)
                        if matched_skill:
                            auto_loaded_skill = matched_skill
                            logger.info(f"Auto-matched skill '{matched_skill}' for file: {att_name}")
                
                    if att_content or att_url:
                        if session_workspace is None:
                            # 工作目录必须落在租户存储目录下，避免在项目根目录产生 skill_ws_* 散落目录
                            ws_tenant_id = identity.tenant_id
                            ws_root = self.workspace_root
                            if ws_root is not None:
                                ws_root.mkdir(parents=True,exist_ok=True)
                            elif ws_tenant_id:
                                from src.core.storage import ensure_tenant_storage_dir
                                ws_root = os.path.abspath(ensure_tenant_storage_dir(ws_tenant_id, "temp"))
                            session_workspace = Path(tempfile.mkdtemp(prefix="skill_ws_", dir=ws_root))
                            logger.info(f"Created session workspace: {session_workspace}")
                    
                        file_path = session_workspace / att_name
                    
                        try:
                            if att_content:
                                file_bytes = base64.b64decode(att_content)
                                file_path.write_bytes(file_bytes)
                                uploaded_files_info.append({
                                    "name": att_name,
                                    "path": str(file_path),
                                    "size": len(file_bytes)
                                })
                                logger.info(f"Saved uploaded file: {file_path} ({len(file_bytes)} bytes)")
                            elif att_url and Path(att_url).exists():
                                import shutil
                                shutil.copy(att_url, file_path)
                                uploaded_files_info.append({
                                    "name": att_name,
                                    "path": str(file_path),
                                    "size": file_path.stat().st_size
                                })
                                logger.info(f"Copied file from URL: {file_path}")
                        except Exception as e:
                            logger.error(f"Failed to save file {att_name}: {e}")
            
                if attachment_info:
                    # 构建文件路径信息（无论是否有 auto_loaded_skill 都添加）
                    files_context = ""
                    if uploaded_files_info:
                        files_context = "\n\n**📎 Uploaded files available:**\n"
                        for f in uploaded_files_info:
                            files_context += f"- File: `{f['name']}`\n"
                            files_context += f"  Full path: `{f['path']}`\n"
                            files_context += f"  Size: {f['size']} bytes\n"
                        files_context += "\n**IMPORTANT: When delegating to subagent, include the file paths above in task_description!**\n"
                
                    enhanced_input = f"{user_input}\n\n[Attachments]\n" + "\n".join(attachment_info) + files_context
        

            injection = ""
            if auto_loaded_skill:
                from src.tools.context import ToolExecutionContext
                skill_context = ToolExecutionContext(tenant_id=identity.tenant_id, user_id=identity.user_id, session_id=identity.session_id)
                skill_content = skill_registry.get_content(auto_loaded_skill, tenant_id=identity.tenant_id, context=skill_context)
                if skill_content:
                    injection = (
                        f'<skill-auto-loaded name="{auto_loaded_skill}">\n{skill_content}\n</skill-auto-loaded>\n\n'
                        'The above skill has been automatically loaded because you received a file that matches this skill.\n'
                        f'{files_context}\nAnalyze the user request and choose the appropriate method from the skill to process the file.\n'
                        'Use `skill_execute` tool to run commands like pdftotext, python scripts, etc.'
                    )
            handed_off = True
            return enhanced_input, injection, str(session_workspace) if session_workspace else None
        finally:
            if session_workspace and not handed_off:
                shutil.rmtree(session_workspace, ignore_errors=True)
