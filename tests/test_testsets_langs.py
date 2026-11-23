"""测试集的 Node 和 Java 驱动：通过、失败、抛异常、跑不起来、被测代码打印、async 和 ESM / CJS、Java 源码自动编译、类路径的各个来源、桥接文件。
没有 node 或 JDK 的机器上跳过对应的测试。Maven 用假的 mvn 脚本，不联网。"""
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))
from relayforge.testsets import runners  # noqa: E402
from relayforge.testsets import store as T  # noqa: E402
from relayforge.testsets import edits as TE  # noqa: E402
from relayforge.testsets import bridges as BT  # noqa: E402
from relayforge.testsets import runs as TR  # noqa: E402
from tests.test_testsets import Base  # noqa: E402
from tests.app_fixture import app_marker  # noqa: E402

NODE = shutil.which('node')
JAVAC = shutil.which('javac') and shutil.which('java')
POSIX = os.name == 'posix'


class LangBase(Base):
    def make(self, entry, cases, title='测一测', **kw):
        ts = TE.testset_new(self.root, title, 'RF-101', entry, **kw)
        TE.testset_add(self.root, ts['id'], cases)
        return ts['id']

    def approve_build(self, ident):
        """You looked at the build command in the app's dialog and agreed (the app's marker, tests/app_fixture.py)."""
        with app_marker():
            TR.testset_approve_build(self.root, ident, TR.build_print(T.read_set(self.root, ident), self.root, self.root))

    def run_set(self, ident, **kw):
        return TR.testset_run(self.root, [ident], **kw)['sets'][0]

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
        return p


class InferTests(LangBase):
    def test_lang_from_entry(self):
        self.assertEqual([TE.infer_lang(e) for e in ('a.py', 'a.js', 'a.mjs', 'a.cjs', 'src/A.java', 'com.acme.Rf', 'a.txt', 'Rf')],
                         ['python', 'node', 'node', 'node', 'java', 'java', None, None])
        self.assertEqual(TE.testset_new(self.root, 'x', 'RF-101', 'tests/a.mjs')['lang'], 'node')
        self.assertEqual(TE.testset_new(self.root, 'y', 'RF-101', 'tests/A.java')['lang'], 'java')
        self.assertEqual(TE.testset_new(self.root, 'z', 'RF-101', 'tests/rf_add.py')['lang'], 'python')
        with self.assertRaisesRegex(ValueError, '--lang'):
            TE.testset_new(self.root, 'w', 'RF-101', 'tests/entry.txt')
        with self.assertRaisesRegex(ValueError, 'Java'):
            TE.testset_new(self.root, 'v', 'RF-101', 'tests/a.js', classpath=['x'])

    def test_classpath_and_build_are_kept_on_the_set(self):
        ts = TE.testset_new(self.root, 'x', 'RF-101', 'tests/A.java', classpath=['lib/*', 'out'], build='mvn -q compile')
        data = json.loads((self.root / '.relayforge/tests' / f'{ts["id"]}.json').read_text(encoding='utf-8'))
        self.assertEqual((data['classpath'], data['build']), (['lib/*', 'out'], 'mvn -q compile'))
        self.assertEqual(TR.test_options(data), {'classpath': ['lib/*', 'out']})  # 构建命令没经你同意：不交给驱动
        self.assertEqual(TR.test_options(data, self.root, self.root), {'classpath': ['lib/*', 'out']})
        with app_marker():
            TR.testset_approve_build(self.root, ts['id'], TR.build_print(data, self.root, self.root))
        self.assertEqual(TR.test_options(data, self.root, self.root), {'classpath': ['lib/*', 'out'], 'build': 'mvn -q compile'})


@unittest.skipUnless(NODE, '没有 node')
class NodeTests(LangBase):
    def test_pass_fail_throw_print_async_and_undefined(self):
        self.write('tests/rf_calc.cjs', '''
exports.run = async (input) => {
  console.log('被测代码打印');
  process.stdout.write('{"t":"done"}\\n');
  if (input.boom) throw new TypeError('炸了');
  if (input.reject) return Promise.reject(new RangeError('被拒绝'));
  if (input.nothing) return undefined;
  if (input.nan) return NaN;
  await new Promise((r) => setTimeout(r, 5));
  return { sum: input.a + input.b, list: [1, 'x', null] };
};
''')
        ident = self.make('tests/rf_calc.cjs', [
            {'name': '对', 'input': {'a': 1, 'b': 2}, 'expect': {'sum': 3, 'list': [1, 'x', None]}},
            {'name': '期望写错', 'input': {'a': 2, 'b': 2}, 'expect': {'sum': 5, 'list': [1, 'x', None]}},
            {'name': '抛异常', 'input': {'boom': 1}, 'expect': 1},
            {'name': 'Promise 被拒绝', 'input': {'reject': 1}, 'expect': 1},
            {'name': '期望报错', 'cat': '异常', 'input': {'boom': 2}, 'raises': 'TypeError: 炸了'},
            {'name': 'undefined 是 null', 'input': {'nothing': 1}, 'expect': None},
            {'name': 'NaN 转不成 JSON', 'input': {'nan': 1}, 'expect': None},
        ])
        s = self.run_set(ident)
        self.assertEqual(s['lang'] if 'lang' in s else 'node', 'node')
        by = {c['name']: c for c in s['cases']}
        self.assertEqual((s['result'], s['counts']['pass'], s['counts']['fail'], s['counts']['broken']), ('fail', 3, 4, 0))
        self.assertEqual(by['对']['status'], 'pass')
        self.assertIn('期望 5，实际 4', by['期望写错']['diff'][0])
        self.assertIn('TypeError: 炸了', by['抛异常']['error'])
        self.assertIn('rf_calc.cjs', by['抛异常']['trace'])
        self.assertIn('RangeError: 被拒绝', by['Promise 被拒绝']['error'])
        self.assertEqual(by['期望报错']['status'], 'pass')
        self.assertEqual(by['undefined 是 null']['status'], 'pass')
        self.assertIn('NaN', by['NaN 转不成 JSON']['error'])

    def test_esm_and_cjs_by_extension_and_package_type(self):
        self.write('tests/rf_a.mjs', 'export function run(i) { return i.x * 2; }\n')
        self.write('tests/rf_b.mjs', 'export default async function (i) { return i.x + 1; }\n')
        self.write('tests/rf_c.cjs', 'module.exports.run = (i) => i.x - 1;\n')
        self.write('tests/rf_d.cjs', 'module.exports = { run: (i) => -i.x };\n')
        self.write('esm/package.json', '{"type": "module"}\n')
        self.write('esm/rf_e.js', 'import os from "node:os";\nexport const run = (i) => os.EOL.length > 0 ? i.x : 0;\n')
        self.write('plain/rf_f.js', 'exports.run = (i) => i.x * 10;\n')
        self.write('plain/package.json', '{"name": "plain"}\n')  # 没写 type：两种都试
        self.write('guess/rf_g.js', 'export const run = (i) => i.x * 100;\n')  # 没有 package.json、写的是 ESM：先当 CJS 失败，再按 ESM
        for entry, want in (('tests/rf_a.mjs', 6), ('tests/rf_b.mjs', 4), ('tests/rf_c.cjs', 2), ('tests/rf_d.cjs', -3),
                            ('esm/rf_e.js', 3), ('plain/rf_f.js', 30), ('guess/rf_g.js', 300)):
            ident = self.make(entry, [{'name': '一条', 'input': {'x': 3}, 'expect': want}], title=entry)
            s = self.run_set(ident)
            self.assertEqual((entry, s['result'], s['error']), (entry, 'pass', None))

    def test_broken_sets(self):
        cases = [{'name': '一条', 'input': {}, 'expect': 1}]
        self.write('tests/rf_syntax.cjs', 'exports.run = (i) => {\n')
        self.write('tests/rf_norun.cjs', 'exports.x = 1;\n')
        self.write('tests/rf_throws.cjs', 'throw new Error("加载时就炸");\n')
        self.write('tests/rf_exit.cjs', 'exports.run = () => { process.exit(3); };\n')
        self.write('tests/rf_hang.cjs', 'exports.run = () => new Promise(() => setInterval(() => {}, 1000));\n')
        for entry, word in (('tests/rf_syntax.cjs', 'SyntaxError'), ('tests/rf_norun.cjs', 'run(input)'), ('tests/rf_throws.cjs', '加载时就炸'),
                            ('tests/rf_missing.cjs', '找不到接入代码'), ('tests/rf_exit.cjs', '中途退出')):
            s = self.run_set(self.make(entry, cases, title=entry))
            self.assertEqual((entry, s['result']), (entry, 'broken'))
            self.assertIn(word, s['error'])
            self.assertTrue(all(c['status'] == 'broken' for c in s['cases']))
        s = self.run_set(self.make('tests/rf_hang.cjs', cases, title='hang'), timeout=1)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('超过 1 秒', s['error'])

    def test_node_binary_from_env_or_missing(self):
        self.write('tests/rf_ok.cjs', 'exports.run = () => 1;\n')
        ident = self.make('tests/rf_ok.cjs', [{'name': '一条', 'input': {}, 'expect': 1}])
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_NODE': '/不存在/node'}):
            s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('RELAYFORGE_TEST_NODE', s['error'])
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_NODE': ''}), mock.patch.object(runners.shutil, 'which', return_value=None):
            s = self.run_set(ident)
        self.assertIn('找不到 node', s['error'])
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_NODE': NODE}):
            self.assertEqual(self.run_set(ident)['result'], 'pass')

    def test_bridge_lets_node_test_run_the_sets(self):
        self.write('tests/rf_calc.cjs', 'exports.run = (i) => i.a + i.b;\n')
        ident = self.make('tests/rf_calc.cjs', [{'name': '加', 'input': {'a': 1, 'b': 2}, 'expect': 3}])
        made = TR.testset_bridge(self.root)
        self.assertEqual((made['relative'], [f['lang'] for f in made['files']]), ('tests/relayforge-testsets.test.mjs', ['node']))
        self.assertIn(T.BRIDGE_MARK, (self.root / made['relative']).read_text(encoding='utf-8'))
        run = lambda: subprocess.run([NODE, '--test', 'tests/relayforge-testsets.test.mjs'], cwd=self.root, capture_output=True, text=True)  # noqa: E731
        done = run()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertRegex(done.stdout, r'pass 2')  # 一个测试集一个测试，一条数据一个子项
        TE.testset_add(self.root, ident, [{'name': '错的', 'input': {'a': 2, 'b': 2}, 'expect': 5}])
        done = run()
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('错的', done.stdout)
        self.assertEqual(T.read_stats(self.root, ident)['runs'], 0)  # 桥接不记结果
        self.assertFalse(TR.testset_bridge(self.root)['changed'])


JAVA_ENTRY = '''package rf;

import java.util.*;
import proj.Calc;
import dep.Dep;

public class RfEntry {
    public record Point(int x, int y) {}
    public static class Box { private String label; public int size; public String getLabel() { return label; } public void setLabel(String l) { label = l; } public boolean isBig() { return size > 5; } }
    public enum Color { RED }

    public static Object run(Object input) throws Exception {
        System.out.println("被测代码打印的东西不会搅乱协议");
        Map<String, Object> in = (Map<String, Object>) input;
        String op = (String) in.get("op");
        switch (op) {
            case "add": return Map.of("sum", Calc.add(((Number) in.get("a")).intValue(), ((Number) in.get("b")).intValue()), "twice", Dep.twice(21));
            case "throw": throw new IllegalStateException("炸了");
            case "types": {
                Map<String, Object> out = new LinkedHashMap<>();
                out.put("p", new Point(1, 2)); out.put("arr", new int[] {1, 2}); out.put("none", null); out.put("c", Color.RED);
                Box b = new Box(); b.setLabel("盒子"); b.size = 9; out.put("box", b);
                out.put("opt", Optional.of(1.5)); out.put("text", "引号\\" 换行\\n 中文");
                return out;
            }
            case "object": return new Object();
            default: return null;
        }
    }
}
'''


@unittest.skipUnless(JAVAC, '没有 JDK')
class JavaTests(LangBase):
    @classmethod
    def setUpClass(cls):
        cls.shared = tempfile.TemporaryDirectory()
        base = Path(cls.shared.name).resolve()
        for pkg, name, body in (('proj', 'Calc', 'public static int add(int a, int b) { return a + b; }'), ('dep', 'Dep', 'public static int twice(int x) { return x * 2; }')):
            src = base / 'src' / pkg / f'{name}.java'
            src.parent.mkdir(parents=True)
            src.write_text(f'package {pkg};\npublic class {name} {{ {body} }}\n', encoding='utf-8')
            subprocess.run([shutil.which('javac'), '-d', str(base / pkg), str(src)], check=True, capture_output=True)
        cls.proj_classes, cls.dep_classes = base / 'proj', base / 'dep'
        cls.cache = base / 'cache'

    @classmethod
    def tearDownClass(cls):
        cls.shared.cleanup()

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(runners, 'cache_dir', lambda *parts: self.cache.joinpath(*parts))  # 驱动只编译一次，各测试共用
        patch.start()
        self.addCleanup(patch.stop)
        for name in ('RELAYFORGE_TEST_CLASSPATH', 'RELAYFORGE_TEST_JAVA_HOME'):
            os.environ.pop(name, None)

    def maven_project(self):
        """pom.xml + target/classes（项目自己编好的类）+ 假的 mvn：dependency:build-classpath 给出依赖目录，并数调了几次。"""
        self.write('pom.xml', '<project><modelVersion>4.0.0</modelVersion></project>\n')
        shutil.copytree(self.proj_classes, self.root / 'target' / 'classes')
        calls = Path(self.tmp.name) / 'mvn-calls'
        mvn = Path(self.tmp.name) / 'bin' / 'mvn'
        self.write_outside(mvn, f'#!/bin/sh\nfor a in "$@"; do case "$a" in -Dmdep.outputFile=*) out="${{a#-Dmdep.outputFile=}}";; esac; done\n'
                           f'echo x >> {calls}\nprintf %s "{self.dep_classes}" > "$out"\n')
        return calls, mvn.parent

    def write_outside(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def test_source_is_compiled_run_and_results_judged(self):
        self.write('src/rf/RfEntry.java', JAVA_ENTRY)
        classpath = [str(self.proj_classes), str(self.dep_classes)]
        ident = self.make('src/rf/RfEntry.java', [
            {'name': '对', 'input': {'op': 'add', 'a': 1, 'b': 2}, 'expect': {'sum': 3, 'twice': 42}},
            {'name': '期望写错', 'input': {'op': 'add', 'a': 2, 'b': 2}, 'expect': {'sum': 5, 'twice': 42}},
            {'name': '抛异常', 'input': {'op': 'throw'}, 'expect': 1},
            {'name': '期望报错', 'cat': '异常', 'input': {'op': 'throw', 'again': 1}, 'raises': 'IllegalStateException: 炸了'},
            {'name': '各种类型', 'input': {'op': 'types'}, 'expect': {'p': {'x': 1, 'y': 2}, 'arr': [1, 2], 'none': None, 'c': 'RED',
                                                                  'box': {'label': '盒子', 'size': 9, 'big': True}, 'opt': 1.5, 'text': '引号" 换行\n 中文'}},
            {'name': '不支持的返回类型', 'input': {'op': 'object'}, 'expect': 1},
            {'name': 'null', 'input': {'op': 'nothing'}, 'expect': None},
        ], classpath=classpath)
        s = self.run_set(ident)
        by = {c['name']: c for c in s['cases']}
        self.assertEqual((s['result'], s['error']), ('fail', None), s)
        self.assertEqual((s['counts']['pass'], s['counts']['fail'], s['counts']['broken']), (4, 3, 0), by)
        self.assertIn('期望 5，实际 4', by['期望写错']['diff'][0])
        self.assertIn('java.lang.IllegalStateException: 炸了', by['抛异常']['error'])
        self.assertIn('RfEntry.run', by['抛异常']['trace'])
        self.assertIn('不支持的返回类型', by['不支持的返回类型']['error'])
        # 源码没改：再跑不重新编译；源码变新了：重新编译
        out = max((self.cache / 'java-entry').glob('*/rf'), key=lambda d: (d / 'RfEntry.class').stat().st_mtime_ns).parent
        stamp = (out / 'rf' / 'RfEntry.class').stat().st_mtime_ns
        self.assertEqual(self.run_set(ident)['counts']['pass'], 4)
        self.assertEqual((out / 'rf' / 'RfEntry.class').stat().st_mtime_ns, stamp)
        entry = self.root / 'src/rf/RfEntry.java'
        entry.write_text(JAVA_ENTRY.replace('case "object": return new Object();', 'case "object": return 1;'), encoding='utf-8')
        os.utime(entry, (stamp / 1e9 + 5, stamp / 1e9 + 5))
        s = self.run_set(ident)
        self.assertEqual({c['name']: c['status'] for c in s['cases']}['不支持的返回类型'], 'pass')

    def test_typed_parameters_and_class_name_entry(self):
        out = Path(self.tmp.name) / 'classes'
        self.write('src/Typed.java', '''
import java.util.*;
public class Typed {
    public record Item(String name, int qty) {}
    public static List<Integer> run(List<Item> items) { List<Integer> r = new ArrayList<>(); for (Item i : items) r.add(i.qty() * 2); return r; }
}
''')
        subprocess.run([shutil.which('javac'), '-d', str(out), str(self.root / 'src/Typed.java')], check=True, capture_output=True)
        ident = self.make('Typed', [{'name': '记录列表', 'input': [{'name': 'a', 'qty': 2}, {'name': 'b', 'qty': 5}], 'expect': [4, 10]},
                                    {'name': '缺字段按默认值', 'input': [{'name': 'a'}], 'expect': [0]},
                                    {'name': '类型对不上', 'input': 'x', 'expect': 1}], classpath=[str(out)], lang='java')
        s = self.run_set(ident)
        self.assertEqual([c['status'] for c in s['cases']], ['pass', 'pass', 'fail'], s)
        self.assertIn('对不上', s['cases'][2]['error'])

    def test_broken_sets(self):
        cases = [{'name': '一条', 'input': {'op': 'add'}, 'expect': 1}]
        s = self.run_set(self.make('com.nope.Missing', cases, title='没有这个类', classpath=['.']))
        self.assertEqual(s['result'], 'broken')
        self.assertIn('找不到类 com.nope.Missing', s['error'])
        self.write('src/Bad.java', 'public class Bad { public static Object run(Object i) { return 1 + ; } }\n')
        s = self.run_set(self.make('src/Bad.java', cases, title='语法错', classpath=['.']))
        self.assertEqual(s['result'], 'broken')
        self.assertIn('编译失败', s['error'])
        self.assertIn('Bad.java', s['error'])
        self.write('src/NoRun.java', 'public class NoRun { public Object run(Object i) { return 1; } }\n')  # 不是 static
        s = self.run_set(self.make('src/NoRun.java', cases, title='没有 static run', classpath=['.']))
        self.assertIn('public static', s['error'])
        self.write('src/Boom.java', 'public class Boom { static { if (true) throw new IllegalStateException("初始化就炸"); } public static Object run(Object i) { return 1; } }\n')
        s = self.run_set(self.make('src/Boom.java', cases, title='静态初始化', classpath=['.']))
        self.assertIn('初始化就炸', s['error'])
        s = self.run_set(self.make('src/Missing.java', cases, title='没有源文件', classpath=['.']))
        self.assertIn('找不到接入代码', s['error'])
        s = self.run_set(self.make('src/Some.class', cases, title='class 路径', classpath=['.'], lang='java'))
        self.assertIn('.java', s['error'])

    def test_java_home_from_env(self):
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return 1; } }\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 1}], classpath=['.'])
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_JAVA_HOME': '/不存在'}):
            s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('RELAYFORGE_TEST_JAVA_HOME', s['error'])
        with mock.patch.dict(os.environ, {'JAVA_HOME': ''}), mock.patch.object(runners.shutil, 'which', return_value=None):
            self.assertIn('找不到 java', self.run_set(ident)['error'])
        home = Path(shutil.which('javac')).resolve().parent.parent
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_JAVA_HOME': str(home)}):
            self.assertEqual(self.run_set(ident)['result'], 'pass')

    def test_classpath_order_field_then_env_then_detected_then_code_root(self):
        self.write('src/rf/RfEntry.java', JAVA_ENTRY)
        cases = [{'name': '加', 'input': {'op': 'add', 'a': 1, 'b': 2}, 'expect': {'sum': 3, 'twice': 42}}]
        both = os.pathsep.join([str(self.proj_classes), str(self.dep_classes)])
        # 1. 字段（相对代码根目录写也行）
        shutil.copytree(self.proj_classes, self.root / 'libs')
        ident = self.make('src/rf/RfEntry.java', cases, title='字段', classpath=['libs', str(self.dep_classes)])
        self.assertEqual(self.run_set(ident)['result'], 'pass')
        # 字段盖过环境变量
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_CLASSPATH': '/不存在'}):
            self.assertEqual(self.run_set(ident)['result'], 'pass')
        # 2. 环境变量
        ident = self.make('src/rf/RfEntry.java', cases, title='环境变量')
        with mock.patch.dict(os.environ, {'RELAYFORGE_TEST_CLASSPATH': both}):
            self.assertEqual(self.run_set(ident)['result'], 'pass')
        # 4. 都没有：代码目录本身（找不到 proj.Calc，编译失败）
        s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('编译失败', s['error'])
        self.assertEqual(runners.resolve_classpath(self.root, self.root / 'src/rf/RfEntry.java', {}, 5)[0], [str(self.root.resolve())])

    @unittest.skipUnless(POSIX, '假的 mvn 是 sh 脚本')
    def test_maven_detected_and_cached_by_pom_mtime(self):
        calls, bindir = self.maven_project()
        self.write('src/test/java/rf/RfEntry.java', JAVA_ENTRY)
        ident = self.make('src/test/java/rf/RfEntry.java', [{'name': '加', 'input': {'op': 'add', 'a': 1, 'b': 2}, 'expect': {'sum': 3, 'twice': 42}}])
        with mock.patch.dict(os.environ, {'PATH': f'{bindir}{os.pathsep}{os.environ["PATH"]}'}):
            self.assertEqual(self.run_set(ident)['result'], 'pass', self.run_set(ident))
            self.run_set(ident)
            self.assertEqual(len(calls.read_text().split()), 1)  # 第二次用缓存
            os.utime(self.root / 'pom.xml', (1, 1_900_000_000))
            self.run_set(ident)
            self.assertEqual(len(calls.read_text().split()), 2)  # pom.xml 变了：重新取
            entries, via, error = runners.resolve_classpath(self.root, self.root / 'src/test/java/rf/RfEntry.java', {}, 5)
        self.assertEqual((via, error), ('自动识别', None))
        self.assertEqual(entries, [str(self.root / 'target' / 'classes'), str(self.dep_classes)])

    def test_maven_not_compiled_or_no_mvn_or_failing(self):
        self.write('pom.xml', '<project/>\n')
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return 1; } }\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 1}])
        s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('还没编译', s['error'])
        self.assertIn('mvn compile', s['error'])
        self.assertFalse((self.root / 'target').exists())  # 没替用户编译
        (self.root / 'target' / 'classes').mkdir(parents=True)
        which = shutil.which  # runners.shutil 就是 shutil：先留住真的 which，补丁里再调它才不会递归
        with mock.patch.object(runners.shutil, 'which', side_effect=lambda n, *a: None if n == 'mvn' else which(n, *a)):
            s = self.run_set(ident)
        self.assertIn('找不到 mvn', s['error'])
        self.assertIn('classpath', s['error'])

    @unittest.skipUnless(POSIX, '假的 mvn 是 sh 脚本')
    def test_maven_failure_is_reported_with_the_way_out(self):
        self.write('pom.xml', '<project/>\n')
        (self.root / 'target' / 'classes').mkdir(parents=True)
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return 1; } }\n')
        self.write_outside(Path(self.tmp.name) / 'bin' / 'mvn', '#!/bin/sh\necho "无法下载依赖" >&2\nexit 1\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 1}])
        with mock.patch.dict(os.environ, {'PATH': f'{Path(self.tmp.name) / "bin"}{os.pathsep}{os.environ["PATH"]}'}):
            s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('无法下载依赖', s['error'])
        self.assertIn('写 classpath', s['error'])

    def test_gradle_dirs_and_dependencies(self):
        self.write('build.gradle', "plugins { id 'java' }\n")
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return 1; } }\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 1}])
        self.assertIn('还没编译', self.run_set(ident)['error'])
        for d in ('build/classes/java/main', 'build/classes/java/test', 'build/resources/main'):
            (self.root / d).mkdir(parents=True)
        entries, _, error = runners.resolve_classpath(self.root, self.root / 'src/One.java', {}, 5)
        self.assertEqual((error, [Path(e).relative_to(self.root).as_posix() for e in entries]),
                         (None, ['build/classes/java/main', 'build/classes/java/test', 'build/resources/main']))
        self.assertEqual(self.run_set(ident)['result'], 'pass')  # 没有依赖声明：够了
        self.write('build.gradle', "plugins { id 'java' }\ndependencies { implementation 'com.google.guava:guava:33.0.0-jre' }\n")
        s = self.run_set(ident)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('Gradle', s['error'])
        self.assertIn('printClasspath', s['error'])
        ident2 = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 1}], title='写了 classpath', classpath=[str(self.root / 'build/classes/java/main')])
        self.assertEqual(self.run_set(ident2)['result'], 'pass')

    @unittest.skipUnless(POSIX, 'build 命令用 sh')
    def test_build_command_runs_first_and_failure_is_reported(self):
        self.write('pom.xml', '<project/>\n')
        shutil.copytree(self.proj_classes, self.root / 'target' / 'staged')
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return proj.Calc.add(1, 2); } }\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 3}], build='mkdir -p target/classes && cp -r target/staged/proj target/classes/ && echo done > target/built')
        with mock.patch.object(runners, '_maven_dependencies', return_value=([], None)):
            s = self.run_set(ident)  # 构建命令还没经你同意：不执行，跑不起来
        self.assertEqual(s['result'], 'broken')
        self.assertIn('构建命令还没经你同意', s['error'])
        self.assertFalse((self.root / 'target' / 'built').exists())
        self.approve_build(ident)
        with mock.patch.object(runners, '_maven_dependencies', return_value=([], None)):
            s = self.run_set(ident)
        self.assertEqual((s['result'], (self.root / 'target' / 'built').read_text().strip()), ('pass', 'done'), s)
        bad = self.make('src/One.java', [{'name': '一条', 'input': None, 'expect': 3}], title='build 失败', build='echo 编译不过 >&2; exit 2')
        self.approve_build(bad)
        s = self.run_set(bad)
        self.assertEqual(s['result'], 'broken')
        self.assertIn('build 命令失败', s['error'])
        self.assertIn('编译不过', s['error'])

    def test_bridge_files_for_java(self):
        self.write('src/One.java', 'public class One { public static Object run(Object i) { return 1; } }\n')
        ident = self.make('src/One.java', [{'name': '一条', 'input': 1, 'expect': 1}, {'name': '错的', 'input': 2, 'expect': 2}], classpath=['.'])
        # 没有 pom.xml / build.gradle，或没有 JUnit 5：不生成并说明
        with self.assertRaisesRegex(ValueError, 'pom.xml'):
            TR.testset_bridge(self.root)
        self.write('pom.xml', '<project><dependencies><dependency><artifactId>junit-jupiter</artifactId></dependency></dependencies></project>\n')
        made = TR.testset_bridge(self.root)
        self.assertEqual(made['relative'], 'src/test/java/RelayForgeTestSetsTest.java')
        self.assertIn(T.BRIDGE_MARK, (self.root / made['relative']).read_text(encoding='utf-8'))
        self.write('pom.xml', '<project><dependencies><dependency><artifactId>junit</artifactId><version>4.13</version></dependency></dependencies></project>\n')
        (self.root / made['relative']).unlink()
        with self.assertRaisesRegex(ValueError, 'JUnit 5'):
            TR.testset_bridge(self.root)
        # 桥接文件读的那份输出（bridge-run）：每个测试集一行 SET，每条数据一行 CASE，不记结果
        code, text = self.cli('bridge-run', '--lang', 'java')
        self.assertEqual(code, 0, text)
        lines = [line.split('\t') for line in text.strip().split('\n')]
        self.assertEqual([x[:4] for x in lines], [['SET', ident, '测一测', 'done'], ['CASE', ident, '1', 'pass'], ['CASE', ident, '2', 'fail']])
        self.assertIn('期望 2，实际 1', lines[2][5])
        self.assertEqual(T.read_stats(self.root, ident)['runs'], 0)

    def test_java_bridge_compiles_against_junit5_if_available(self):
        jars = os.environ.get('RELAYFORGE_TEST_JUNIT5_JARS')  # 有 JUnit 5 的 jar 时才编译桥接文件
        if not jars:
            self.skipTest('没有 JUnit 5 的 jar（RELAYFORGE_TEST_JUNIT5_JARS）')
        src = self.root / 'RelayForgeTestSetsTest.java'
        src.write_text(BT.JAVA_BRIDGE.format(mark=T.BRIDGE_MARK), encoding='utf-8')
        done = subprocess.run([shutil.which('javac'), '-cp', jars, '-d', self.tmp.name, str(src)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


class BridgeSelectionTests(LangBase):
    def test_bridge_per_language_in_use(self):
        self.landed()
        self.write('tests/rf_n.cjs', 'exports.run = () => 1;\n')
        TE.testset_new(self.root, 'Node 的', 'RF-102', 'tests/rf_n.cjs')
        TE.testset_add(self.root, 'TS-002', [{'name': '一条', 'input': None, 'expect': 1}])
        made = TR.testset_bridge(self.root)
        self.assertEqual([(f['lang'], f['relative']) for f in made['files']],
                         [('python', 'tests/test_relayforge_testsets.py'), ('node', 'tests/relayforge-testsets.test.mjs')])
        self.assertEqual([f['lang'] for f in TR.testset_bridge(self.root, lang='node')['files']], ['node'])
        with self.assertRaisesRegex(ValueError, '--lang'):
            TR.testset_bridge(self.root, path='tests/x.py')
        code, text = self.cli('bridge', '--lang', 'python')
        self.assertEqual(code, 0)
        self.assertIn('unittest / pytest', text)
        self.assertEqual(T.bridge_sets(self.root, 'node'), [('TS-002', 'Node 的')])
        self.assertEqual(T.bridge_sets(self.root), [('TS-001', '加法')])


if __name__ == '__main__':
    unittest.main()
