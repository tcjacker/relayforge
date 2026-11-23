"""知识保鲜（facts/fresh.py）：功能点对应哪些代码、哪些可能过时、核对会话交回建议、你采纳或忽略、核对完。"""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
import rf  # noqa: E402
from relayforge.facts import fresh as F  # noqa: E402
from tests.test_testsets import Base, git  # noqa: E402


class FreshTests(Base):
    def finish(self, features, text, name='calc.py'):
        rid, place = self.requirement(features)
        (place / name).write_text(text, encoding='utf-8')
        git(place, 'add', '-A')
        git(place, 'commit', '-qm', 'work')
        rf.req_done(self.root, rid, skip_gate=True)
        return rid

    def direct(self, name, text, message):
        (self.root / name).parent.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(text, encoding='utf-8')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-qm', message)
        return git(self.root, 'rev-parse', '--short', 'HEAD').strip()

    def stale(self, fid):
        return next(r for r in F.fresh_drift(self.root) if r['id'] == fid)

    def test_files_come_from_finished_work_and_only_foreign_meaningful_commits_count(self):
        self.assertTrue(self.stale('RF-101')['unmapped'])
        self.finish('RF-101 加法：改一改', 'def add(a, b):\n    return b + a\n')
        mapping, _ = F.feature_files(self.root)
        self.assertEqual(list(mapping['RF-101']), ['calc.py'])
        self.assertFalse(self.stale('RF-101')['stale'])  # 改它的需求自己做的不算
        self.direct('calc.py', 'def add(a, b):\n    # 加法\n    return b + a\n', 'comment')  # 只改注释
        self.direct('tests/test_calc.py', 'x = 1\n', 'tests')  # 测试文件
        self.assertFalse(self.stale('RF-101')['stale'])
        short = self.direct('calc.py', 'def add(a, b):\n    # 加法\n    return a + b + 0\n', 'tweak add')
        row = self.stale('RF-101')
        self.assertTrue(row['stale'])
        self.assertEqual([(c['short'], c['owner'], c['files']) for c in row['commits']], [(short, None, ['calc.py'])])
        self.assertIn('tweak add', F.fresh_brief(self.root, 'RF-101'))
        # 另一个也说要改 RF-101 的需求改了它：不算过时的来源；改别的功能点的需求改了它：算
        self.finish('RF-102 导出：顺手改了加法', 'def add(a, b):\n    return a + b + 1\n')
        owners = {c['owner'] for c in self.stale('RF-101')['commits']}
        self.assertEqual(len(owners - {None}), 1)

    def test_a_check_session_proposes_the_user_adopts_or_ignores_then_marks_it_checked(self):
        self.finish('RF-101 加法：改一改', 'def add(a, b):\n    return b + a\n')
        self.direct('calc.py', 'def add(a, b):\n    return round(a + b, 2)\n', 'round')
        path = next((self.root / rf.DIR / 'features').glob('RF-101-*.md'))
        meta, sections = rf.read_doc(path)
        sections['规则与边界'] = ['- 结果是整数〔S-x#1〕']
        rf.write_doc(path, meta, sections)
        rf.commit_facts(self.root, 'rules')
        payload = {'verdict': 'changed', 'summary': '加法现在保留两位小数', 'items': [
            {'section': '规则与边界', 'op': 'replace', 'old': '结果是整数', 'new': '结果保留两位小数', 'why': 'round 提交改成了 round(a + b, 2)'},
            {'section': '调整时要注意', 'op': 'add', 'new': '改精度会影响导出', 'why': '推测'}]}
        with self.assertRaisesRegex(ValueError, '只有为 RF-101 开的核对会话'):
            F.fresh_propose(self.root, 'RF-101', payload)
        me = {'verified': True, 'kind': 'check', 'owner': 'RF-101', 'session': 'sid-1', 'why': ''}
        with mock.patch.object(F, 'caller', return_value=me):
            with self.assertRaisesRegex(ValueError, 'why'):
                F.fresh_propose(self.root, 'RF-101', {'verdict': 'changed', 'items': [{'section': '规则与边界', 'op': 'add', 'new': 'x'}]})
            made = F.fresh_propose(self.root, 'RF-101', payload)
        self.assertEqual([i['state'] for i in made['items']], ['pending', 'pending'])
        with self.assertRaisesRegex(ValueError, '还有 2 条建议没处理'):
            F.fresh_done(self.root, 'RF-101')
        F.fresh_adopt(self.root, 'RF-101', ['1'])
        F.fresh_ignore(self.root, 'RF-101', ['2'])
        text = path.read_text(encoding='utf-8')
        self.assertIn('〔已替代〕结果是整数', text)
        self.assertIn('结果保留两位小数〔', text)
        self.assertNotIn('改精度会影响导出', text)
        self.assertTrue(self.stale('RF-101')['stale'])
        F.fresh_done(self.root, 'RF-101')
        self.assertFalse(self.stale('RF-101')['stale'])  # 核对到当前提交：同样的提交不再算
        self.assertIsNone(F.fresh_show(self.root, 'RF-101'))
        self.assertIn('核对到', path.read_text(encoding='utf-8'))

    def test_changing_code_of_a_feature_not_declared_is_pointed_out(self):
        from relayforge.requirements.conflicts import conflicts
        self.finish('RF-101 加法：改一改', 'def add(a, b):\n    return b + a\n')
        self.finish('RF-102 导出：加导出', 'def export():\n    return 1\n', 'export.py')
        rid, place = self.requirement('RF-102 导出：再改导出')
        (place / 'export.py').write_text('def export():\n    return 2\n', encoding='utf-8')  # 自己的范围：不算
        self.assertEqual(next(i for i in conflicts(self.root)['items'] if i['id'] == rid)['outside'], [])
        (place / 'calc.py').write_text('def add(a, b):\n    return a + b + 2\n', encoding='utf-8')  # 加法的代码
        item = next(i for i in conflicts(self.root)['items'] if i['id'] == rid)
        self.assertEqual(item['outside'], [{'feature': 'RF-101', 'files': ['calc.py']}])

    def test_the_user_only_commands_and_the_proxy_list(self):
        from relayforge.core.trust import USER_COMMANDS
        from relayforge.core.proxy import PROXIED
        for a in ('adopt', 'ignore', 'done'):
            self.assertIn(('fresh', a), USER_COMMANDS)
        self.assertIn(('fresh', 'propose'), PROXIED)


if __name__ == '__main__':
    unittest.main()
