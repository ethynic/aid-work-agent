"""
OCR工具

实现图片和PDF文字识别功能
"""

import base64
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from src.tools.base import BaseTool
from src.config.settings import settings


class OCRImageTool(BaseTool):
    """图片OCR识别工具"""
    
    name = "ocr_image"
    description = "识别图片中的文字内容"
    category = "ocr"
    parameters_schema = {
        "type": "object",
        "properties": {
            "image_url": {
                "type": "string",
                "description": "图片URL地址",
            },
            "image_base64": {
                "type": "string",
                "description": "图片Base64编码（可选）",
            },
        },
        "required": [],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行图片OCR识别
        
        Args:
            image_url: 图片URL
            image_base64: 图片Base64编码
        
        Returns:
            识别结果
        """
        image_url = kwargs.get("image_url", "")
        image_base64 = kwargs.get("image_base64", "")
        
        if not image_url and not image_base64:
            return {
                "success": False,
                "error": "请提供图片URL或Base64编码",
            }
        
        # 获取OCR配置
        config = settings.tools.ocr
        
        if config.provider == "baidu":
            return await self._baidu_ocr(image_url, image_base64, config)
        else:
            return {
                "success": False,
                "error": f"不支持的OCR提供商: {config.provider}",
            }
    
    async def _baidu_ocr(
        self,
        image_url: str,
        image_base64: str,
        config,
    ) -> Dict[str, Any]:
        """
        百度OCR识别
        
        Args:
            image_url: 图片URL
            image_base64: 图片Base64编码
            config: OCR配置
        
        Returns:
            识别结果
        """
        try:
            # 获取access_token
            async with httpx.AsyncClient() as client:
                token_url = "https://aip.baidubce.com/oauth/2.0/token"
                token_response = await client.post(
                    token_url,
                    params={
                        "grant_type": "client_credentials",
                        "client_id": config.baidu_api_key,
                        "client_secret": config.baidu_secret_key,
                    }
                )
                token_data = token_response.json()
                access_token = token_data.get("access_token")
                
                if not access_token:
                    return {
                        "success": False,
                        "error": "获取百度OCR访问令牌失败",
                    }
                
                # 调用OCR API
                ocr_url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic?access_token={access_token}"
                
                if image_url:
                    ocr_response = await client.post(
                        ocr_url,
                        data={"url": image_url}
                    )
                else:
                    ocr_response = await client.post(
                        ocr_url,
                        data={"image": image_base64}
                    )
                
                result = ocr_response.json()
                
                if "error_code" in result:
                    return {
                        "success": False,
                        "error": f"OCR识别失败: {result.get('error_msg', '未知错误')}",
                    }
                
                # 提取文字
                words_list = result.get("words_result", [])
                text = "\n".join([item.get("words", "") for item in words_list])
                
                logger.info(f"OCR识别成功，识别出{len(words_list)}行文字")
                
                return {
                    "success": True,
                    "text": text,
                    "lines": len(words_list),
                    "message": f"成功识别出{len(words_list)}行文字",
                }
        
        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return {
                "success": False,
                "error": f"OCR识别失败: {str(e)}",
            }


class OCRPdfTool(BaseTool):
    """PDF OCR识别工具"""
    
    name = "ocr_pdf"
    description = "识别PDF文档中的文字内容"
    category = "ocr"
    parameters_schema = {
        "type": "object",
        "properties": {
            "pdf_url": {
                "type": "string",
                "description": "PDF文件URL地址",
            },
            "pages": {
                "type": "string",
                "description": "要识别的页码范围，如'1-5'或'all'，默认为all",
            },
        },
        "required": ["pdf_url"],
    }
    
    async def execute(self, **kwargs) -> Dict[str, Any]:
        """
        执行PDF OCR识别
        
        Args:
            pdf_url: PDF文件URL
            pages: 页码范围
        
        Returns:
            识别结果
        """
        pdf_url = kwargs.get("pdf_url", "")
        pages = kwargs.get("pages", "all")
        
        if not pdf_url:
            return {
                "success": False,
                "error": "请提供PDF文件URL",
            }
        
        # PDF OCR需要更复杂的处理，这里提供简化实现
        # 实际项目中可以使用专门的PDF处理服务
        
        try:
            # 模拟PDF处理（实际需要实现PDF解析和OCR）
            logger.info(f"PDF OCR识别: {pdf_url}")
            
            return {
                "success": True,
                "text": "PDF内容识别功能需要配置PDF处理服务。当前为演示模式。",
                "message": "PDF识别功能需要额外配置",
                "note": "请配置PDF处理服务以启用完整功能",
            }
        
        except Exception as e:
            logger.error(f"PDF OCR识别失败: {e}")
            return {
                "success": False,
                "error": f"PDF OCR识别失败: {str(e)}",
            }
