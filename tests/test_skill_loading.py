#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from src.core.skill_loader import SkillLoader
from pathlib import Path

loader = SkillLoader(Path('src/skills'))
print('Skills:', loader.list_skills())

for name, skill in loader.skills.items():
    print(f'\nSkill: {name}')
    print(f'  Description: {skill.description[:100]}...')
    print(f'  Paths: {skill.paths}')
    print(f'  Context mode: {skill.context_mode}')
    print(f'  Argument hint: {skill.argument_hint}')

print('\nMatch by file test.pdf:', loader.match_by_file('test.pdf'))
