#!/usr/bin/env python3
"""Fix escaped strings in app.py"""

import re

with open('src/gui/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix all escaped quotes in string literals
content = content.replace('\\"', '"')

with open('src/gui/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Fixed all escaped quotes in app.py")
