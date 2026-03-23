from datetime import datetime as dt
import re
import sys

t = dt.now().strftime('%Y%m%d%H%M')
filepath = 'frontend/vite.config.ts'

with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

content = re.sub(r"VERSION\s*=\s*'[^']*'", "VERSION = '%s'" % t, content)

with open(filepath, 'w', encoding='utf-8') as f:
    f.write(content)

print(t)