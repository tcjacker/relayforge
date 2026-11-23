"""应用在提示中使用的自然语言设置。"""

import json
import os

from .common import relayforge_home

_INSTRUCTIONS = {
    'en': ('Use English for every user-facing reply, progress update, and conclusion. '
           'In structured output, use English for user-facing prose and keep the required keys, enum values and IDs. '
           'Preserve commands, code, identifiers, and quotations.'),
    'zh-CN': ('使用简体中文撰写每条面向用户的回复、进度和结论。结构化输出中面向用户的正文也用简体中文，'
              '保留约定的字段名、枚举值及编号。保留命令、代码、标识符及引用原文。'),
}


def reply_language():
    """每一轮都读当前设置，让已经打开的会话也能收到语言更新。"""
    try:
        saved = json.loads((relayforge_home() / 'settings.json').read_text(encoding='utf-8'))
        language = saved.get('language') if isinstance(saved, dict) else None
        if isinstance(language, str) and language in _INSTRUCTIONS:
            return _INSTRUCTIONS[language]
    except (OSError, ValueError):
        pass
    return _INSTRUCTIONS.get(os.environ.get('RELAYFORGE_LANGUAGE'), '')



def display_text(zh, en):
    """只翻译应用这次调用中展示的文字，命令行的旧输出与数据键沿用原有格式。"""
    return en if os.environ.get('RELAYFORGE_LANGUAGE') == 'en' else zh
