"""修复验证用的 git 检查：当前在哪个提交、修复提交是不是已经在当前分支上、受跟踪的代码有没有没提交的改动。

AI 排查时报告「已验证修好」（issues.issue_finding）和批量复核（issue_review）都要做这几样检查。分层见 tests/test_layers.py。
"""
import re

from ..core.common import run_git

__all__ = ['fixed_verification']


def snapshot(root):
    branch = run_git(root, 'symbolic-ref', '--quiet', '--short', 'HEAD').stdout.strip()
    commit = run_git(root, 'rev-parse', '--verify', 'HEAD').stdout.strip()
    return {'branch': branch or None, 'commit': commit or None}


def is_commit(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{40}|[a-f0-9]{64}', value)


def contains(root, fix, head):
    return bool(is_commit(fix) and is_commit(head)
                and run_git(root, 'merge-base', '--is-ancestor', fix, head).returncode == 0)


def clean(root):
    status = run_git(root, '--no-optional-locks', 'status', '--porcelain', '--untracked-files=no',
                     '--', '.', ':(top,exclude).relayforge')
    return status.returncode == 0 and not status.stdout


def fixed_verification(root, fix, steps, verified):
    """Tie a positive verification report to the actual committed code and fix."""
    if not (steps and verified):
        raise ValueError('验证修复需要写明原复现步骤和实际验证通过的结果。')
    head = snapshot(root)['commit']
    if not contains(root, fix, head) or not clean(root) or snapshot(root)['commit'] != head:
        raise ValueError('修复提交须在当前分支上，验证时不能有未提交的受跟踪代码改动。请填写完整提交编号。')
    return {'fix': fix, 'head': head}
