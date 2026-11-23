"""测试集 Node、Java 驱动的启动准备：找运行时、解析类路径、编译驱动和接入代码。Python 驱动不需要这些，在 testsets.py 里直接启动。

prepare(...) 给 testsets.drive 返回 (命令, 环境变量, 请求里多带的字段, 错误)。错误不为空就是这个测试集「跑不起来」，
写明原因和该怎么办。规则见 FORMAT.md 的「测试集」：

  Node   RELAYFORGE_TEST_NODE，否则 PATH 里的 node。
  Java   java / javac：RELAYFORGE_TEST_JAVA_HOME，否则 JAVA_HOME，否则 PATH。驱动源码（drivers/RelayForgeDriver.java）运行时编译到缓存目录。
         类路径：测试集的 classpath 字段 → 环境变量 RELAYFORGE_TEST_CLASSPATH → 自动识别（Maven、Gradle）→ 代码目录本身。
         接入代码是 .java 源文件时，对应的 class 不存在或比源码旧就用 javac 编译到缓存目录。
缓存放在 RELAYFORGE_HOME（默认 ~/.relayforge）/cache/testsets/ 下，不进仓库。
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from ..core.common import PACKAGE, relayforge_home
from ..core import procs
from ..core.trust import scrubbed_environ

NODE_DRIVER = PACKAGE / 'drivers' / 'node_driver.mjs'
JAVA_DRIVER = PACKAGE / 'drivers' / 'RelayForgeDriver.java'
JAVA_DRIVER_CLASS = 'RelayForgeDriver'
BUILD_TIMEOUT = 600  # 测试集里写了 build 命令时，它最多跑多久
PICKED_UP = re.compile(r'^Picked up [A-Z_]+:.*$', re.M)
GRADLE_DEP = re.compile(r'\b(implementation|api|compile|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|testCompile|annotationProcessor)\b\s*[(\'"\[\w]')


def cache_dir(*parts):
    path = relayforge_home() / 'cache' / 'testsets'
    for p in parts:
        path = path / p
    return path


def _digest(*parts):
    h = hashlib.sha1()
    for p in parts:
        h.update(str(p).encode('utf-8', 'replace'))
        h.update(b'\0')
    return h.hexdigest()[:16]


def _tail(text, cap=3000):
    text = PICKED_UP.sub('', text or '').strip()
    return text[-cap:]


def _run(command, cwd, timeout, env=None, shell=False):
    """(returncode, output) or (None, 说明) when it could not start or timed out."""
    try:  # shell=True 的构建命令超时时，只杀 sh 会留下 mvn、gradle、java：连同整棵树一起停
        code, out, err = procs.run(command, cwd=str(cwd), timeout=timeout, env=env if env is not None else scrubbed_environ(), shell=shell)
    except OSError as error:
        return None, str(error)
    if code is None:
        return None, f'超过 {timeout} 秒没有跑完'
    return code, (out + err).decode('utf-8', errors='replace')


# ---------- Node ----------

def node_for():
    chosen = os.environ.get('RELAYFORGE_TEST_NODE')
    if chosen:
        found = shutil.which(chosen) or (chosen if Path(chosen).is_file() else None)
        if not found:
            return None, f'环境变量 RELAYFORGE_TEST_NODE 指向的 {chosen} 不存在或不能运行。'
        return found, None
    found = shutil.which('node')
    if not found:
        return None, '找不到 node：请安装 Node.js，或用环境变量 RELAYFORGE_TEST_NODE 指定 node 的路径。'
    return found, None


def prepare_node(place, entry):
    node, error = node_for()
    if error:
        return None, {}, {}, error
    if Path(entry).suffix.lower() not in ('.js', '.mjs', '.cjs'):
        return None, {}, {}, f'Node 的接入代码要是 .js、.mjs 或 .cjs 文件，{entry} 不是。'
    return [node, str(NODE_DRIVER)], {}, {}, None


# ---------- Java ----------

def java_tools():
    """(java, javac, error)"""
    homes = [os.environ.get('RELAYFORGE_TEST_JAVA_HOME'), os.environ.get('JAVA_HOME')]
    exe = '.exe' if os.name == 'nt' else ''
    for i, home in enumerate(homes):
        if not home:
            continue
        java, javac = Path(home) / 'bin' / f'java{exe}', Path(home) / 'bin' / f'javac{exe}'
        if java.is_file() and javac.is_file():
            return str(java), str(javac), None
        if i == 0:
            return None, None, f'环境变量 RELAYFORGE_TEST_JAVA_HOME={home} 下没有 bin/java 和 bin/javac（要装 JDK，不是只有 JRE）。'
        # JAVA_HOME 不可用时往下走到 PATH
    java, javac = shutil.which('java'), shutil.which('javac')
    if not java or not javac:
        missing = 'java' if not java else 'javac'
        return None, None, f'找不到 {missing}：请安装 JDK，或用环境变量 RELAYFORGE_TEST_JAVA_HOME（其次 JAVA_HOME）指定 JDK 的位置。'
    return java, javac, None


def compile_driver(javac, timeout=120):
    """Compile the bundled driver into the cache (keyed by its source and the javac that built it). (dir, error)"""
    try:
        source = JAVA_DRIVER.read_text(encoding='utf-8')
        stat = Path(javac).resolve().stat()
    except OSError as error:
        return None, f'读不到 Java 驱动的源码或 javac：{error}'
    out = cache_dir('java-driver', _digest(source, Path(javac).resolve(), stat.st_size, stat.st_mtime_ns))
    if (out / f'{JAVA_DRIVER_CLASS}.class').is_file():
        return out, None
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='build-', dir=out.parent))
    try:
        code, text = _run([javac, '-encoding', 'UTF-8', '-proc:none', '-nowarn', '-d', str(work), str(JAVA_DRIVER)], work, timeout)
        if code != 0:
            return None, f'Java 驱动编译失败（javac {javac}）：\n{_tail(text)}'
        try:
            work.rename(out)
        except OSError:  # 别的进程同时编好了
            if not (out / f'{JAVA_DRIVER_CLASS}.class').is_file():
                raise
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out, None


def _split_classpath(value, place):
    """A classpath string or list → absolute entries (relative ones are under the code root; `dir/*` kept)."""
    items = []
    for part in ([value] if isinstance(value, str) else list(value or [])):
        items.extend(p for p in str(part).split(os.pathsep) if p.strip())
    out = []
    for p in items:
        p = p.strip()
        out.append(p if os.path.isabs(p) else str(Path(place) / p))
    return out


def project_of(place, entry_path):
    """(directory, 'maven'|'gradle') of the nearest pom.xml / build.gradle[.kts] above the entry, within the code root; or (None, None)."""
    place = Path(place).resolve()
    d = Path(entry_path).resolve().parent
    while True:
        if (d / 'pom.xml').is_file():
            return d, 'maven'
        if any((d / n).is_file() for n in ('build.gradle', 'build.gradle.kts', 'settings.gradle', 'settings.gradle.kts')):
            return d, 'gradle'
        if d == place or place not in d.parents:
            return None, None
        d = d.parent


def _maven_dependencies(project, timeout):
    """`mvn -q dependency:build-classpath`, cached by pom.xml's modification time. (entries, error)"""
    pom = project / 'pom.xml'
    cache = cache_dir('maven', _digest(project.resolve()) + '.json')
    stamp = pom.stat().st_mtime_ns
    try:
        saved = json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('pom_mtime_ns') == stamp and saved.get('pom') == str(pom.resolve()):
            return saved['entries'], None
    except (OSError, ValueError, KeyError):
        pass
    hint = '请在测试集里写 classpath（或设环境变量 RELAYFORGE_TEST_CLASSPATH），或先自己运行 mvn dependency:build-classpath -Dmdep.outputFile=cp.txt，把结果填进 classpath。'
    mvn = shutil.which('mvn') or (str(project / 'mvnw') if (project / 'mvnw').is_file() and os.access(project / 'mvnw', os.X_OK) else None)
    if not mvn:
        return None, f'这是 Maven 项目（{pom}），但找不到 mvn。{hint}'
    fd, outfile = tempfile.mkstemp(prefix='cp-', suffix='.txt')
    os.close(fd)
    try:
        code, text = _run([mvn, '-q', '-B', 'dependency:build-classpath', f'-Dmdep.outputFile={outfile}'], project, timeout)
        if code != 0:
            return None, f'mvn dependency:build-classpath 失败（在 {project}）：{_tail(text, 1500)}\n{hint}'
        entries = [p for p in Path(outfile).read_text(encoding='utf-8').strip().split(os.pathsep) if p]
    finally:
        try:
            os.unlink(outfile)
        except OSError:
            pass
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'pom': str(pom.resolve()), 'pom_mtime_ns': stamp, 'entries': entries}), encoding='utf-8')
    except OSError:
        pass
    return entries, None


def auto_classpath(place, entry_path, timeout):
    """Entries found from the project itself. (entries, error): Maven → target dirs + mvn's dependency classpath; Gradle → build dirs
    (its dependencies cannot be fetched automatically); neither → the code root."""
    project, kind = project_of(place, entry_path)
    if not project:
        return [str(Path(place).resolve())], None
    if kind == 'maven':
        dirs = [project / 'target' / 'classes', project / 'target' / 'test-classes']
        compile_hint = f'先在 {project} 里运行 mvn compile（RelayForge 不替你编译），或在测试集里写 build 命令（它会在每次运行前先跑）'
    else:
        dirs = [project / 'build' / 'classes' / 'java' / 'main', project / 'build' / 'classes' / 'java' / 'test']
        dirs += sorted(p for p in (project / 'build' / 'resources').glob('*') if p.is_dir())
        compile_hint = f'先在 {project} 里运行 gradle classes（RelayForge 不替你编译），或在测试集里写 build 命令（它会在每次运行前先跑）'
    found = [str(d) for d in dirs if d.is_dir()]
    if not (project / ('target' if kind == 'maven' else 'build') / 'classes').is_dir():
        return None, f'{kind.capitalize()} 项目 {project} 还没编译（没有{"target/classes" if kind == "maven" else "build/classes"}）：请{compile_hint}。'
    if kind == 'maven':
        deps, error = _maven_dependencies(project, timeout)
        if error:
            return None, error
        return found + deps, None
    text = ''.join((project / n).read_text(encoding='utf-8', errors='replace') for n in ('build.gradle', 'build.gradle.kts') if (project / n).is_file())
    if GRADLE_DEP.search(text):
        return None, (f'这是 Gradle 项目（{project}），依赖没法自动取得。请在测试集里写 classpath（或设环境变量 RELAYFORGE_TEST_CLASSPATH）：'
                      "比如在 build.gradle 里加 tasks.register('printClasspath') { doLast { println sourceSets.test.runtimeClasspath.asPath } }，"
                      '运行 gradle -q printClasspath，把输出填进 classpath。')
    return found, None


def resolve_classpath(place, entry_path, options, timeout):
    """(entries, source, error): the test set's classpath field → RELAYFORGE_TEST_CLASSPATH → detected from the project → the code root."""
    if options.get('classpath'):
        return _split_classpath(options['classpath'], place), '测试集的 classpath 字段', None
    if os.environ.get('RELAYFORGE_TEST_CLASSPATH'):
        return _split_classpath(os.environ['RELAYFORGE_TEST_CLASSPATH'], place), '环境变量 RELAYFORGE_TEST_CLASSPATH', None
    entries, error = auto_classpath(place, entry_path, timeout)
    return entries, '自动识别', error


def java_class_of(place, entry):
    """(class name, source file or None, error) for the entry: a .java file (class name from its package line), or a class name."""
    entry = str(entry)
    if entry.endswith('.java'):
        source = Path(place) / entry
        if not source.is_file():
            return None, None, f'找不到接入代码 {entry}（在 {place} 下）。'
        text = source.read_text(encoding='utf-8', errors='replace')
        text = re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)
        m = re.search(r'^\s*package\s+([\w.]+)\s*;', text, re.M)
        return (m.group(1) + '.' if m else '') + source.stem, source, None
    if entry.endswith('.class'):
        return None, None, f'Java 的接入代码请写 .java 源文件的路径，或类的全名（比如 com.acme.RfBills）；不收 .class 文件的路径（{entry}）。'
    if re.fullmatch(r'[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*', entry):
        return entry, None, None
    return None, None, f'没法从 {entry} 得到 Java 类名：写 .java 源文件的路径，或类的全名。'


def _class_file(dirs, name):
    rel = name.replace('.', '/') + '.class'
    for d in dirs:
        p = Path(d) / rel
        if p.is_file():
            return p
    return None


def prepare_java(place, entry, options, timeout):
    java, javac, error = java_tools()
    if error:
        return None, {}, {}, error
    cls, source, error = java_class_of(place, entry)
    if error:
        return None, {}, {}, error
    if options.get('build'):  # 写了 build 命令就先运行它（比如 mvn -q compile）；没写就不替用户编译
        code, text = _run(str(options['build']), place, BUILD_TIMEOUT, shell=True)
        if code != 0:
            return None, {}, {}, f'build 命令失败（{options["build"]}）：{_tail(text, 2500) if code is not None else text}'
    entry_path = source or Path(place) / 'x'
    entries, via, error = resolve_classpath(place, entry_path, options, timeout)
    if error:
        return None, {}, {}, error
    driver_dir, error = compile_driver(javac)
    if error:
        return None, {}, {}, error
    compiled = []
    if source is not None:
        mtime = source.stat().st_mtime
        dirs = [e for e in entries if Path(e).is_dir()]
        have = _class_file(dirs, cls)
        if not have or have.stat().st_mtime < mtime:
            out = cache_dir('java-entry', _digest(Path(place).resolve(), entry, os.pathsep.join(entries)))
            have = _class_file([out], cls)
            if not have or have.stat().st_mtime < mtime:
                out.mkdir(parents=True, exist_ok=True)
                code, text = _run([javac, '-encoding', 'UTF-8', '-proc:none', '-nowarn', '-d', str(out), '-cp', os.pathsep.join(entries) or '.', str(source)], place, timeout)
                if code != 0:
                    return None, {}, {}, f'接入代码 {entry} 编译失败（类路径来自{via}）：\n{_tail(text)}'
            compiled = [str(out)]
    path = os.pathsep.join([str(driver_dir), *compiled, *entries])
    return [java, '-Dfile.encoding=UTF-8', '-XX:TieredStopAtLevel=1', '-cp', path, JAVA_DRIVER_CLASS], {}, {'class': cls}, None


def prepare(lang, place, entry, options=None, timeout=120):
    """(command, env updates, extra request fields, error) for the node and java drivers."""
    options = options or {}
    if lang == 'node':
        return prepare_node(place, entry)
    if lang == 'java':
        return prepare_java(place, entry, options, timeout)
    return None, {}, {}, f'没有 {lang} 的驱动。'
