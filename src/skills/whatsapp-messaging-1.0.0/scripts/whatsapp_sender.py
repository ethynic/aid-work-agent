#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WhatsApp消息发送脚本

支持发送多种类型的WhatsApp消息：
- 文本消息
- 图片、视频、音频、文档消息
- 模板消息
- 交互式消息（按钮、列表）
- 位置消息
- 联系人消息
- 贴纸消息

使用WhatsApp Cloud API (WhatsApp Business API)
文档: https://developers.facebook.com/docs/whatsapp
"""

import os
import sys
import json
import argparse
import mimetypes
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

import requests
from dotenv import load_dotenv

# 加载环境变量
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)


class WhatsAppAPI:
    """WhatsApp Business API客户端"""
    
    API_BASE_URL = "https://graph.facebook.com/v18.0"
    
    def __init__(self, access_token: Optional[str] = None, phone_number_id: Optional[str] = None):
        """
        初始化WhatsApp API客户端
        
        Args:
            access_token: WhatsApp Access Token（可从环境变量获取）
            phone_number_id: WhatsApp Phone Number ID（可从环境变量获取）
        """
        self.access_token = access_token or os.getenv("WHATSAPP_ACCESS_TOKEN")
        self.phone_number_id = phone_number_id or os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        
        if not self.access_token:
            raise ValueError("WHATSAPP_ACCESS_TOKEN not configured. Please set it in .env file or pass as parameter.")
        
        if not self.phone_number_id:
            raise ValueError("WHATSAPP_PHONE_NUMBER_ID not configured. Please set it in .env file or pass as parameter.")
        
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        })
    
    def send_message(self, to: str, message_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送消息
        
        Args:
            to: 接收者手机号（格式：国家码+号码，如8613800138000）
            message_data: 消息数据（根据消息类型不同而不同）
            
        Returns:
            API响应数据
        """
        url = f"{self.API_BASE_URL}/{self.phone_number_id}/messages"
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            **message_data
        }
        
        response = self.session.post(url, json=payload, timeout=30)
        
        if response.status_code != 200:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
            error_code = error_data.get("error", {}).get("code", response.status_code)
            raise Exception(f"API错误 ({error_code}): {error_msg}")
        
        return response.json()
    
    def send_text(self, to: str, text: str, preview_url: bool = False) -> Dict[str, Any]:
        """发送文本消息"""
        message_data = {
            "type": "text",
            "text": {
                "preview_url": preview_url,
                "body": text
            }
        }
        return self.send_message(to, message_data)
    
    def send_image(self, to: str, image: str, caption: Optional[str] = None) -> Dict[str, Any]:
        """
        发送图片消息
        
        Args:
            to: 接收者手机号
            image: 图片URL或本地文件路径
            caption: 图片说明
        """
        # 判断是URL还是本地文件
        if image.startswith("http://") or image.startswith("https://"):
            image_data = {"link": image}
        else:
            # 本地文件需要先上传
            image_id = self._upload_media(image)
            image_data = {"id": image_id}
        
        text_data = {"type": "image", "image": image_data}
        
        if caption:
            text_data["image"]["caption"] = caption
        
        return self.send_message(to, text_data)
    
    def send_video(self, to: str, video: str, caption: Optional[str] = None) -> Dict[str, Any]:
        """发送视频消息"""
        if video.startswith("http://") or video.startswith("https://"):
            video_data = {"link": video}
        else:
            video_id = self._upload_media(video)
            video_data = {"id": video_id}
        
        text_data = {"type": "video", "video": video_data}
        
        if caption:
            text_data["video"]["caption"] = caption
        
        return self.send_message(to, text_data)
    
    def send_audio(self, to: str, audio: str) -> Dict[str, Any]:
        """发送音频消息"""
        if audio.startswith("http://") or audio.startswith("https://"):
            audio_data = {"link": audio}
        else:
            audio_id = self._upload_media(audio)
            audio_data = {"id": audio_id}
        
        message_data = {"type": "audio", "audio": audio_data}
        return self.send_message(to, message_data)
    
    def send_document(
        self, 
        to: str, 
        document: str, 
        filename: Optional[str] = None,
        caption: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送文档消息"""
        if document.startswith("http://") or document.startswith("https://"):
            doc_data = {"link": document}
        else:
            doc_id = self._upload_media(document)
            doc_data = {"id": doc_id}
        
        if filename:
            doc_data["filename"] = filename
        
        if caption:
            doc_data["caption"] = caption
        
        message_data = {"type": "document", "document": doc_data}
        return self.send_message(to, message_data)
    
    def send_sticker(self, to: str, sticker: str) -> Dict[str, Any]:
        """发送贴纸消息（WebP格式）"""
        if sticker.startswith("http://") or sticker.startswith("https://"):
            sticker_data = {"link": sticker}
        else:
            sticker_id = self._upload_media(sticker)
            sticker_data = {"id": sticker_id}
        
        message_data = {"type": "sticker", "sticker": sticker_data}
        return self.send_message(to, message_data)
    
    def send_template(
        self, 
        to: str, 
        template_name: str, 
        language: str = "en_US",
        components: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """
        发送模板消息
        
        Args:
            to: 接收者手机号
            template_name: 模板名称
            language: 语言代码（zh_CN, en_US等）
            components: 模板组件（参数）
        """
        template_data = {
            "name": template_name,
            "language": {"code": language}
        }
        
        if components:
            template_data["components"] = components
        
        message_data = {
            "type": "template",
            "template": template_data
        }
        
        return self.send_message(to, message_data)
    
    def send_interactive_buttons(
        self,
        to: str,
        body: str,
        buttons: List[Dict[str, Any]],
        header: Optional[Dict[str, Any]] = None,
        footer: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发送交互式按钮消息
        
        Args:
            to: 接收者手机号
            body: 消息正文
            buttons: 按钮配置
            header: 消息头部（可选）
            footer: 消息底部（可选）
        """
        interactive_data = {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": buttons}
        }
        
        if header:
            interactive_data["header"] = header
        
        if footer:
            interactive_data["footer"] = {"text": footer}
        
        message_data = {
            "type": "interactive",
            "interactive": interactive_data
        }
        
        return self.send_message(to, message_data)
    
    def send_interactive_list(
        self,
        to: str,
        body: str,
        button_text: str,
        sections: List[Dict[str, Any]],
        header: Optional[Dict[str, Any]] = None,
        footer: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        发送交互式列表消息
        
        Args:
            to: 接收者手机号
            body: 消息正文
            button_text: 列表按钮文字
            sections: 列表选项
            header: 消息头部（可选）
            footer: 消息底部（可选）
        """
        interactive_data = {
            "type": "list",
            "body": {"text": body},
            "action": {
                "button": button_text,
                "sections": sections
            }
        }
        
        if header:
            interactive_data["header"] = header
        
        if footer:
            interactive_data["footer"] = {"text": footer}
        
        message_data = {
            "type": "interactive",
            "interactive": interactive_data
        }
        
        return self.send_message(to, message_data)
    
    def send_location(
        self,
        to: str,
        latitude: float,
        longitude: float,
        name: Optional[str] = None,
        address: Optional[str] = None
    ) -> Dict[str, Any]:
        """发送位置消息"""
        location_data = {
            "latitude": latitude,
            "longitude": longitude
        }
        
        if name:
            location_data["name"] = name
        
        if address:
            location_data["address"] = address
        
        message_data = {
            "type": "location",
            "location": location_data
        }
        
        return self.send_message(to, message_data)
    
    def send_contacts(self, to: str, contacts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """发送联系人消息"""
        message_data = {
            "type": "contacts",
            "contacts": contacts
        }
        
        return self.send_message(to, message_data)
    
    def _upload_media(self, file_path: str) -> str:
        """
        上传媒体文件到WhatsApp服务器
        
        Args:
            file_path: 本地文件路径
            
        Returns:
            媒体文件ID
        """
        path = Path(file_path)
        
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")
        
        # 获取MIME类型
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"
        
        url = f"{self.API_BASE_URL}/{self.phone_number_id}/media"
        
        headers = {
            "Authorization": f"Bearer {self.access_token}"
        }
        
        with open(path, "rb") as f:
            files = {
                "file": (path.name, f, mime_type),
                "messaging_product": (None, "whatsapp"),
                "type": (None, mime_type)
            }
            
            response = requests.post(url, headers=headers, files=files, timeout=60)
        
        if response.status_code != 200:
            error_data = response.json() if response.content else {}
            error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
            raise Exception(f"上传失败: {error_msg}")
        
        result = response.json()
        return result["id"]
    
    def mark_as_read(self, message_id: str) -> Dict[str, Any]:
        """
        标记消息为已读
        
        Args:
            message_id: 消息ID
            
        Returns:
            API响应
        """
        url = f"{self.API_BASE_URL}/{self.phone_number_id}/messages"
        
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id
        }
        
        response = self.session.post(url, json=payload, timeout=30)
        return response.json()


def format_response(response: Dict[str, Any], pretty: bool = False) -> str:
    """格式化API响应"""
    if pretty:
        return json.dumps(response, indent=2, ensure_ascii=False)
    return json.dumps(response, ensure_ascii=False)


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="WhatsApp消息发送工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 发送文本消息
  python %(prog)s --to "8613800138000" --message "您好，订单已确认"

  # 发送图片
  python %(prog)s --to "8613800138000" --type image --image "https://example.com/img.jpg" --caption "产品图片"

  # 发送模板消息
  python %(prog)s --to "8613800138000" --type template --template-name "order_confirmation" --template-language "zh_CN"

  # 发送按钮消息
  python %(prog)s --to "8613800138000" --type interactive --interactive-type button \\
    --interactive-body "请选择配送方式" --interactive-buttons '[{"type":"reply","reply":{"id":"express","title":"快递"}}]'
        """
    )
    
    # 基本参数
    parser.add_argument("--to", required=True, help="接收者手机号（国家码+号码）")
    parser.add_argument("--type", default="text", choices=[
        "text", "image", "video", "audio", "document", "sticker",
        "template", "interactive", "location", "contacts"
    ], help="消息类型")
    parser.add_argument("--pretty", action="store_true", help="格式化输出JSON")
    
    # 文本消息参数
    parser.add_argument("--message", help="文本消息内容")
    parser.add_argument("--preview-url", action="store_true", help="预览URL")
    
    # 媒体消息参数
    parser.add_argument("--image", help="图片URL或路径")
    parser.add_argument("--video", help="视频URL或路径")
    parser.add_argument("--audio", help="音频URL或路径")
    parser.add_argument("--document", help="文档URL或路径")
    parser.add_argument("--sticker", help="贴纸URL或路径")
    parser.add_argument("--caption", help="媒体说明文字")
    parser.add_argument("--filename", help="文档文件名")
    
    # 模板消息参数
    parser.add_argument("--template-name", help="模板名称")
    parser.add_argument("--template-language", default="en_US", help="模板语言代码")
    parser.add_argument("--template-components", help="模板参数（JSON格式）")
    
    # 交互式消息参数
    parser.add_argument("--interactive-type", choices=["button", "list"], help="交互类型")
    parser.add_argument("--interactive-body", help="交互消息正文")
    parser.add_argument("--interactive-buttons", help="按钮配置（JSON格式）")
    parser.add_argument("--interactive-list-button", help="列表按钮文字")
    parser.add_argument("--interactive-list-sections", help="列表选项（JSON格式）")
    parser.add_argument("--interactive-header-type", choices=["text", "image", "video", "document"], help="头部类型")
    parser.add_argument("--interactive-header-text", help="头部文本")
    parser.add_argument("--interactive-header-image", help="头部图片URL")
    parser.add_argument("--interactive-header-video", help="头部视频URL")
    parser.add_argument("--interactive-header-document", help="头部文档URL")
    parser.add_argument("--interactive-footer", help="消息底部文字")
    
    # 位置消息参数
    parser.add_argument("--location-latitude", type=float, help="纬度")
    parser.add_argument("--location-longitude", type=float, help="经度")
    parser.add_argument("--location-name", help="位置名称")
    parser.add_argument("--location-address", help="地址详情")
    
    # 联系人消息参数
    parser.add_argument("--contacts", help="联系人信息（JSON格式）")
    
    # API配置
    parser.add_argument("--access-token", help="WhatsApp Access Token（可选，默认从环境变量读取）")
    parser.add_argument("--phone-number-id", help="WhatsApp Phone Number ID（可选，默认从环境变量读取）")
    
    args = parser.parse_args()
    
    try:
        # 创建API客户端
        api = WhatsAppAPI(
            access_token=args.access_token,
            phone_number_id=args.phone_number_id
        )
        
        response = None
        
        # 根据消息类型发送
        if args.type == "text":
            if not args.message:
                parser.error("--message is required for text type")
            response = api.send_text(args.to, args.message, args.preview_url)
        
        elif args.type == "image":
            if not args.image:
                parser.error("--image is required for image type")
            response = api.send_image(args.to, args.image, args.caption)
        
        elif args.type == "video":
            if not args.video:
                parser.error("--video is required for video type")
            response = api.send_video(args.to, args.video, args.caption)
        
        elif args.type == "audio":
            if not args.audio:
                parser.error("--audio is required for audio type")
            response = api.send_audio(args.to, args.audio)
        
        elif args.type == "document":
            if not args.document:
                parser.error("--document is required for document type")
            response = api.send_document(args.to, args.document, args.filename, args.caption)
        
        elif args.type == "sticker":
            if not args.sticker:
                parser.error("--sticker is required for sticker type")
            response = api.send_sticker(args.to, args.sticker)
        
        elif args.type == "template":
            if not args.template_name:
                parser.error("--template-name is required for template type")
            
            components = None
            if args.template_components:
                components = json.loads(args.template_components)
            
            response = api.send_template(
                args.to,
                args.template_name,
                args.template_language,
                components
            )
        
        elif args.type == "interactive":
            if not args.interactive_type:
                parser.error("--interactive-type is required for interactive type")
            if not args.interactive_body:
                parser.error("--interactive-body is required for interactive type")
            
            # 构建头部
            header = None
            if args.interactive_header_type:
                if args.interactive_header_type == "text" and args.interactive_header_text:
                    header = {"type": "text", "text": args.interactive_header_text}
                elif args.interactive_header_type == "image" and args.interactive_header_image:
                    header = {"type": "image", "image": {"link": args.interactive_header_image}}
                elif args.interactive_header_type == "video" and args.interactive_header_video:
                    header = {"type": "video", "video": {"link": args.interactive_header_video}}
                elif args.interactive_header_type == "document" and args.interactive_header_document:
                    header = {"type": "document", "document": {"link": args.interactive_header_document}}
            
            if args.interactive_type == "button":
                if not args.interactive_buttons:
                    parser.error("--interactive-buttons is required for button type")
                
                buttons = json.loads(args.interactive_buttons)
                response = api.send_interactive_buttons(
                    args.to,
                    args.interactive_body,
                    buttons,
                    header,
                    args.interactive_footer
                )
            
            elif args.interactive_type == "list":
                if not args.interactive_list_button:
                    parser.error("--interactive-list-button is required for list type")
                if not args.interactive_list_sections:
                    parser.error("--interactive-list-sections is required for list type")
                
                sections = json.loads(args.interactive_list_sections)
                response = api.send_interactive_list(
                    args.to,
                    args.interactive_body,
                    args.interactive_list_button,
                    sections,
                    header,
                    args.interactive_footer
                )
        
        elif args.type == "location":
            if not args.location_latitude or not args.location_longitude:
                parser.error("--location-latitude and --location-longitude are required for location type")
            
            response = api.send_location(
                args.to,
                args.location_latitude,
                args.location_longitude,
                args.location_name,
                args.location_address
            )
        
        elif args.type == "contacts":
            if not args.contacts:
                parser.error("--contacts is required for contacts type")
            
            contacts_data = json.loads(args.contacts)
            response = api.send_contacts(args.to, contacts_data)
        
        # 输出结果
        print(format_response(response, args.pretty))
        
    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "error": f"JSON解析错误: {e}"}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
