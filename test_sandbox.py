#!/usr/bin/env python3
import sys
import asyncio
import base64
import tempfile
from pathlib import Path

sys.path.insert(0, '.')

from src.core.agent import MasterAgent
from src.core.sandbox import SandboxManager, SkillSandboxConfig


async def test_sandbox():
    sandbox = SandboxManager(prefer_docker=False)
    config = SkillSandboxConfig()
    
    workdir = Path(tempfile.mkdtemp(prefix="test_sandbox_"))
    
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
    
    pdf_path = workdir / "test.pdf"
    pdf_path.write_bytes(pdf_content)
    
    print(f"Workdir: {workdir}")
    print(f"PDF file: {pdf_path}")
    
    print("\n1. Testing pypdf...")
    cmd1 = 'python -c "from pypdf import PdfReader; r = PdfReader(\\"test.pdf\\"); print([p.extract_text() for p in r.pages])"'
    result1 = await sandbox.execute_command(cmd1, config, workdir)
    print(f"Success: {result1.success}")
    print(f"Stdout: {result1.stdout}")
    print(f"Stderr: {result1.stderr}")
    
    print("\n2. Testing pdfplumber...")
    cmd2 = 'python -c "import pdfplumber; pdf = pdfplumber.open(\\"test.pdf\\"); print([p.extract_text() for p in pdf.pages])"'
    result2 = await sandbox.execute_command(cmd2, config, workdir)
    print(f"Success: {result2.success}")
    print(f"Stdout: {result2.stdout}")
    print(f"Stderr: {result2.stderr}")


if __name__ == "__main__":
    asyncio.run(test_sandbox())
