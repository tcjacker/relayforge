"""桥接文件的模板：rf.py test bridge 生成进项目仓库的 Python / Node / Java 桥接文件，让项目自己的测试命令也跑到测试集；以及给 Java 桥接读的结果行。

只是文字模板，不依赖别的模块。生成和运行在 testruns.py；桥接文件导入的 bridge_sets、bridge_run 留在 testsets.py（对外兼容）。
"""
import re


__all__ = ['bridge_lines']


BRIDGE = '''"""由 RelayForge 生成（rf.py test bridge）：让 unittest / pytest 也跑到 .relayforge/tests/ 下的测试集。{mark}

每个在用的 Python 测试集是一个测试方法，每条数据是一个 subTest。数据和比对都由 RelayForge 的程序做（同一个执行器），
这里只是把结果交给测试框架；这样跑不记运行结果。找不到 RelayForge 时整个文件跳过。不要手工改，重新生成即可。
"""
import os
import sys
import unittest
from pathlib import Path


def _relayforge():
    for d in [Path(__file__).resolve().parent, *Path(__file__).resolve().parents]:
        script = d / '.relayforge' / 'bin' / 'rf.py'
        if script.is_file():
            return d, script.parent
    return None, None


ROOT, BIN = _relayforge()
if BIN is None:
    raise unittest.SkipTest('没有找到 .relayforge/bin/rf.py，跳过测试集')
sys.path.insert(0, str(BIN))
try:
    from relayforge.testsets import bridge_sets, bridge_run  # noqa: E402
finally:
    sys.path.remove(str(BIN))


class RelayForgeTestSets(unittest.TestCase):
    maxDiff = None


def _method(ident):
    def test(self):
        out = bridge_run(ROOT, ident)
        if out['result'] == 'broken':
            self.fail(f'{{ident}} 跑不起来：{{out["error"]}}')
        for row in out['cases']:
            with self.subTest(f'#{{row["n"]}} {{row["name"]}}'):
                if row['status'] != 'pass':
                    self.fail('；'.join(row['diff']) or row.get('error') or '失败')
    return test


for _ident, _title in bridge_sets(ROOT):
    setattr(RelayForgeTestSets, 'test_' + _ident.replace('-', '_'), _method(_ident))
'''


NODE_BRIDGE = """// 由 RelayForge 生成（rf.py test bridge）：让 node --test 也跑到 .relayforge/tests/ 下的 Node 测试集。{mark}
// 每个在用的 Node 测试集是一个测试，每条数据是一个子项。数据和比对都由 RelayForge 的程序做（rf.py test bridge-run，同一个执行器），
// 这里只是把结果交给 node:test；这样跑不记运行结果。找不到 RelayForge 时整个文件跳过。不要手工改，重新生成即可。
import test from 'node:test';
import assert from 'node:assert/strict';
import {{ spawnSync }} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {{ fileURLToPath }} from 'node:url';

const findRoot = () => {{
  let dir = path.dirname(fileURLToPath(import.meta.url));
  for (;;) {{
    if (fs.existsSync(path.join(dir, '.relayforge', 'bin', 'rf.py'))) return dir;
    const up = path.dirname(dir);
    if (up === dir) return null;
    dir = up;
  }}
}};

const root = findRoot();
if (!root) {{
  test('RelayForge 测试集', {{ skip: '没有找到 .relayforge/bin/rf.py，跳过测试集' }}, () => {{}});
}} else {{
  const python = process.env.RELAYFORGE_PYTHON || 'python3';
  const done = spawnSync(python, [path.join(root, '.relayforge', 'bin', 'rf.py'), 'test', 'bridge-run', '--lang', 'node', '--json'],
    {{ cwd: root, encoding: 'utf8', maxBuffer: 1 << 28, env: {{ ...process.env, PYTHONIOENCODING: 'utf-8' }} }});
  let sets = [];
  let failure = null;
  try {{
    if (done.error) throw done.error;
    sets = JSON.parse(done.stdout).sets;
  }} catch (error) {{
    failure = `没能运行 ${{python}} .relayforge/bin/rf.py test bridge-run：${{error.message}}\\n${{done.stderr || ''}}`;
  }}
  if (failure) test('RelayForge 测试集', () => assert.fail(failure));
  for (const set of sets) {{
    test(`${{set.id}} ${{set.title}}`, async (t) => {{
      if (set.result === 'broken') assert.fail(`跑不起来：${{set.error}}`);
      for (const row of set.cases) {{
        await t.test(`#${{row.n}} ${{row.name}}`, () => {{
          if (row.status !== 'pass') assert.fail((row.diff || []).join('；') || row.error || '失败');
        }});
      }}
    }});
  }}
}}
"""


JAVA_BRIDGE = """// 由 RelayForge 生成（rf.py test bridge）：让 JUnit 5 也跑到 .relayforge/tests/ 下的 Java 测试集。{mark}
// 每个在用的 Java 测试集是一个 DynamicContainer，每条数据是一个 DynamicTest。数据和比对都由 RelayForge 的程序做
// （rf.py test bridge-run，同一个执行器），这里只是把结果交给 JUnit；这样跑不记运行结果。找不到 RelayForge 时整个类跳过。
// 不要手工改，重新生成即可。
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.DynamicContainer;
import org.junit.jupiter.api.DynamicNode;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.TestFactory;

public class RelayForgeTestSetsTest {{
    static String unescape(String s) {{
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {{
            char c = s.charAt(i);
            if (c == '\\\\' && i + 1 < s.length()) {{
                char n = s.charAt(++i);
                sb.append(n == 'n' ? '\\n' : n);
            }} else {{
                sb.append(c);
            }}
        }}
        return sb.toString();
    }}

    @TestFactory
    List<DynamicNode> testSets() throws Exception {{
        File root = new File(System.getProperty("user.dir")).getAbsoluteFile();
        while (root != null && !new File(root, ".relayforge/bin/rf.py").isFile()) root = root.getParentFile();
        Assumptions.assumeTrue(root != null, "没有找到 .relayforge/bin/rf.py，跳过测试集");
        String python = System.getenv("RELAYFORGE_PYTHON") != null ? System.getenv("RELAYFORGE_PYTHON") : "python3";
        ProcessBuilder pb = new ProcessBuilder(python, ".relayforge/bin/rf.py", "test", "bridge-run", "--lang", "java");
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        Process p = pb.directory(root).redirectError(ProcessBuilder.Redirect.INHERIT).start();
        List<String> lines = new ArrayList<String>();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {{
            for (String line; (line = r.readLine()) != null; ) lines.add(line);
        }}
        int code = p.waitFor();
        List<DynamicNode> sets = new ArrayList<DynamicNode>();
        String id = null, title = null, result = null, error = null;
        List<DynamicNode> cases = new ArrayList<DynamicNode>();
        for (String line : lines) {{
            String[] f = line.split("\\t", -1);
            if (f[0].equals("SET") && f.length >= 5) {{
                if (id != null) sets.add(container(id, title, result, error, cases));
                id = f[1]; title = unescape(f[2]); result = f[3]; error = unescape(f[4]);
                cases = new ArrayList<DynamicNode>();
            }} else if (f[0].equals("CASE") && f.length >= 6) {{
                final String status = f[3], message = unescape(f[5]);
                cases.add(DynamicTest.dynamicTest("#" + f[2] + " " + unescape(f[4]), () -> {{
                    if (!status.equals("pass")) throw new AssertionError(message.isEmpty() ? "失败" : message);
                }}));
            }}
        }}
        if (id != null) sets.add(container(id, title, result, error, cases));
        if (code != 0 && sets.isEmpty()) {{
            sets.add(DynamicTest.dynamicTest("RelayForge 测试集", () -> {{ throw new AssertionError("rf.py test bridge-run 退出码 " + code); }}));
        }}
        return sets;
    }}

    static DynamicNode container(String id, String title, String result, String error, List<DynamicNode> cases) {{
        if (result.equals("broken")) {{
            return DynamicContainer.dynamicContainer(id + " " + title, java.util.Collections.singletonList(
                DynamicTest.dynamicTest("跑不起来", () -> {{ throw new AssertionError(error); }})));
        }}
        return DynamicContainer.dynamicContainer(id + " " + title, cases);
    }}
}}
"""


def _line_text(value):
    return str(value or '').replace('\\', '\\\\').replace('\t', ' ').replace('\r', '').replace('\n', '\\n')


def bridge_lines(report):
    """The report as tab-separated lines (the Java bridge reads this, it has no JSON library to count on)."""
    out = []
    for s in report['sets']:
        out.append('\t'.join(['SET', s['id'], _line_text(s['title']), s['result'], _line_text(s['error'])]))
        for r in s['cases']:
            out.append('\t'.join(['CASE', s['id'], str(r['n']), r['status'], _line_text(r['name']), _line_text('；'.join(r['diff']) or r.get('error'))]))
    return '\n'.join(out)


JUNIT5 = re.compile(r'junit-jupiter|org\.junit\.jupiter|spring-boot-starter-test|junit-platform|junit-bom')
