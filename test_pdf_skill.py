#!/usr/bin/env python3
import sys
import asyncio
import base64
import tempfile
from pathlib import Path

sys.path.insert(0, '.')

from src.core.agent import MasterAgent


async def test_pdf_skill():
    agent = MasterAgent()
    
    pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj
<< /Length 44 >>
stream
BT
/F1 12 Tf
100 700 Td
(Hello World!) Tj
ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000359 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
434
%%EOF
"""
    
    pdf_b64 = base64.b64encode(pdf_content).decode('utf-8')
    
    print("=" * 60)
    print("Testing PDF Skill Integration")
    print("=" * 60)
    
    print(f"\n1. Skills loaded: {agent.skill_registry.list_skills()}")
    
    pdf_skill = agent.skill_registry.get("pdf")
    if pdf_skill:
        print(f"2. PDF skill found: {pdf_skill.description[:100]}...")
        print(f"   Triggers: {[t.pattern for t in pdf_skill.triggers]}")
    
    matched = agent.skill_registry.match_by_file("test.pdf")
    print(f"3. Match skill for 'test.pdf': {matched}")
    
    print("\n4. Simulating file upload with user question...")
    
    attachments = [{
        "type": "file",
        "name": "test.pdf",
        "mime_type": "application/pdf",
        "content": pdf_b64
    }]
    
    print("\n5. Processing message...")
    print("-" * 60)
    
    response = await agent.process_message_sync(
        user_input="请提取这个PDF中的文字内容",
        session_id="test_session_001",
        attachments=attachments
    )
    
    print("\nResponse:")
    print(response)
    print("-" * 60)
    
    print("\nTest completed!")


if __name__ == "__main__":
    asyncio.run(test_pdf_skill())
