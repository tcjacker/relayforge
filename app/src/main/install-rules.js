// 安装的分级放行：「处理环境」的会话（「让 AI 来处理」开的聊天）和开发、修复会话里，装依赖、编译、起服务这类命令分三级。
//   allow 预先放行：项目目录里的安装和构建、只读的查看命令；
//   ask   每次都要你点同意：系统级的安装和会改电脑状态的命令（不放行也不禁止，Claude Code 每次弹出来问你；写成 ask 规则，
//         这样上面 allow 里的前缀（比如 npm install）也盖不过它们（npm install -g 照样要问）；
//   deny  一律禁止：发布、部署、推送，和读取或输出保密值的途径。
// 规则是纯数据（命令的写法，* 是任意文字，结尾的 :* 是「以它开头」，和 Claude Code 的权限规则同一种写法），
// rules() 把它们变成 --settings 里的权限规则，classify() 按同样的写法判断一条命令落在哪一级（单测用它覆盖每一级的代表命令）。
const path = require('path');

const PROJECT_BUILD = [
  // Node：不带 -g 的安装（带 -g 的在 ask 里，ask 先于 allow）
  'npm ci:*', 'npm install:*', 'npm i:*', 'npm run build:*', 'pnpm install:*', 'pnpm i:*', 'yarn install:*', 'yarn', 'npx tsc:*',
  // Python：建项目里的虚拟环境、往里装
  'python3 -m venv:*', 'python -m venv:*', '.venv/bin/pip install:*', '.venv/bin/pip3 install:*', '.venv/bin/python -m pip install:*', 'venv/bin/pip install:*',
  './.venv/bin/pip install:*', './venv/bin/pip install:*',
  // Java：不部署的目标
  'mvn compile:*', 'mvn test-compile:*', 'mvn test:*', 'mvn package:*', 'mvn clean:*', 'mvn -q:*', 'mvn -B:*', 'mvn -o:*',
  './mvnw compile:*', './mvnw test-compile:*', './mvnw test:*', './mvnw package:*', './mvnw clean:*', './mvnw -q:*', './mvnw -B:*',
  // dependency:* 写成完整的目标名：「:*」在权限规则里是「以它开头」的意思，dependency 后面紧跟的冒号算不算词的边界说不准
  ...['build-classpath', 'copy-dependencies', 'tree', 'resolve', 'go-offline', 'list'].flatMap((g) => [`mvn dependency:${g}:*`, `./mvnw dependency:${g}:*`]),
  'gradle build:*', 'gradle classes:*', 'gradle testClasses:*', 'gradle dependencies:*', 'gradle -q:*',
  './gradlew build:*', './gradlew classes:*', './gradlew testClasses:*', './gradlew dependencies:*', './gradlew -q:*',
  'javac:*',
];
const LOOK = [
  'which:*', 'command -v:*', 'type:*', 'uname:*', 'java -version', 'java --version', 'javac -version', 'javac --version', '/usr/libexec/java_home:*',
  'node --version', 'node -v', 'npm --version', 'npm -v', 'pnpm --version', 'yarn --version', 'python3 --version', 'python --version', 'pip --version', 'pip3 --version',
  'mvn -v', 'mvn --version', './mvnw -v', './mvnw --version', 'gradle --version', './gradlew --version',
  'docker ps:*', 'docker images:*', 'docker version:*', 'docker info:*', 'docker compose ps:*', 'docker logs:*',
];
const SYSTEM = [
  'sudo:*', 'brew:*', 'apt:*', 'apt-get:*', 'port:*', 'snap:*', 'dnf:*', 'yum:*',
  // 装到全局（ask 先于 allow，所以 npm install -g 不会被 npm install:* 放行）
  'npm install -g:*', 'npm i -g:*', 'npm * -g', 'npm * -g *', 'npm * --global*', 'pnpm add -g:*', 'pnpm * -g *', 'yarn global:*',
  // pip 装进系统环境（项目里的 .venv/bin/pip 在 allow）
  'pip install:*', 'pip3 install:*', 'python -m pip install:*', 'python3 -m pip install:*', 'pip uninstall:*', 'pip3 uninstall:*', 'pipx:*',
  // 容器：起、拉
  'docker run:*', 'docker start:*', 'docker pull:*', 'docker compose up:*', 'docker compose * up*', 'docker-compose up:*', 'docker-compose * up*', 'docker build:*',
  // 运行时版本管理
  'sdk:*', 'nvm:*', 'pyenv:*', 'asdf:*', 'rbenv:*', 'jenv:*', 'volta install:*', 'corepack enable:*',
  // 改 shell 配置
  '* >> ~/.zshrc*', '* > ~/.zshrc*', '* >> ~/.bashrc*', '* > ~/.bashrc*', '* >> ~/.bash_profile*', '* >> ~/.profile*', '* >> ~/.zprofile*',
  '*tee* ~/.zshrc*', '*tee* ~/.bashrc*',
];
const SHELL_FILES = ['~/.zshrc', '~/.zprofile', '~/.bashrc', '~/.bash_profile', '~/.profile', '~/.config/fish/config.fish'];
const PUBLISH = [
  'mvn deploy:*', 'mvn *deploy*', 'mvn release:*', 'mvn *release*', './mvnw deploy:*', './mvnw *deploy*', './mvnw *release*',
  'gradle publish*', 'gradle * publish*', './gradlew publish*', './gradlew * publish*',
  'npm publish:*', 'npm * publish', 'pnpm publish:*', 'yarn publish:*', 'yarn npm publish:*',
  'docker push:*', 'docker image push:*', 'docker * push *',
];

// 读取或输出保密值的途径：本机存保密值的目录、应用的标记登记（只有散列，也不让读）
function secretPaths(rfHome) {
  const homes = [rfHome, '~/.relayforge', '$HOME/.relayforge'].filter(Boolean);
  return [...new Set(homes)].flatMap((h) => [`*${h}/envs*`, `*${h}/apps*`]);
}

function rules(rfHome) {
  const bash = (x) => `Bash(${x})`;
  return {
    allow: [...PROJECT_BUILD, ...LOOK].map(bash),
    ask: [...SYSTEM.map(bash), ...SHELL_FILES.map((f) => `Edit(${f})`)], // Edit 规则管所有改文件的工具，Write(路径) 规则永远对不上
    // 应用的登记目录（apps）不禁读：Claude Code 会把 Read 禁止套到沙箱上，会话里的 rf.py 就找不到应用（见 claude-settings.js 的 USER_DENY 上面的说明）。
    // 命令里直接写出这个目录的（cat、ls 之类）照样禁止（secretPaths），写由沙箱挡住
    deny: [...PUBLISH.map(bash), ...secretPaths(rfHome).map(bash), ...(rfHome ? [`Read(/${path.resolve(rfHome)}/envs/**)`] : [])],
  };
}

// 和 Claude Code 一样的写法：结尾 :* 是「以它开头」（后面是空格或结束），其余的 * 是任意文字，没有 * 就要整条相同
function matches(pattern, command) {
  const cmd = String(command).trim();
  if (pattern.endsWith(':*') && !pattern.slice(0, -2).includes('*')) {
    const pre = pattern.slice(0, -2);
    return cmd === pre || cmd.startsWith(pre + ' ');
  }
  const body = pattern.endsWith(':*') ? pattern.slice(0, -2) + '*' : pattern;
  const re = new RegExp('^' + body.split('*').map((x) => x.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$');
  return re.test(cmd);
}
// 一条 shell 命令落在哪一级：deny 先于 ask，ask 先于 allow（Claude Code 也是这个顺序）；都不是就是 null（照 Claude Code 默认的问你）。
// 用 && ; | 连起来的，按最严的那一段算
function classify(command, rfHome) {
  const r = rules(rfHome);
  const strip = (list) => list.filter((x) => x.startsWith('Bash(')).map((x) => x.slice(5, -1));
  const tiers = [['deny', strip(r.deny)], ['ask', strip(r.ask)], ['allow', strip(r.allow)]];
  const one = (cmd) => { for (const [name, list] of tiers) if (list.some((p) => matches(p, cmd))) return name; return null; };
  const parts = String(command).split(/\s*(?:&&|\|\||;|\|)\s*/).filter(Boolean);
  const order = ['deny', 'ask', null, 'allow'];
  return parts.map(one).sort((a, b) => order.indexOf(a) - order.indexOf(b))[0] || null;
}

// 提示词里的三级（「让 AI 来处理」的聊天、开发和修复会话）
const GUIDE = '装东西和跑命令分三级：'
  + '一、项目目录里的安装和构建预先放行，不用问我——npm ci / npm install（不带 -g）、pnpm install、yarn install、建项目里的虚拟环境（python3 -m venv .venv）并用 .venv/bin/pip install 装、'
  + 'mvn / ./mvnw 的 compile、test-compile、test、package、dependency:*、gradle / ./gradlew 的 build、classes、testClasses、dependencies、javac，以及查看版本和状态的命令（java -version、which、docker ps 之类）；'
  + '二、系统级的安装和会改电脑状态的命令每次都要我点同意——brew、apt / apt-get、任何 sudo、npm -g、pip install 到系统环境、docker run / docker compose up / docker pull、sdk / nvm / pyenv / asdf 装版本、改 ~/.zshrc 之类的 shell 配置；'
  + '要做这类事之前，先用一句话告诉我装什么、为什么要装、装到哪里，等我同意；能在项目目录里解决的就不要装到系统里；'
  + '三、一律禁止——mvn deploy / release、npm publish、docker push、git push，以及读取或输出保密值（本机的保密值文件、rf.py env reveal）。';

module.exports = { rules, classify, matches, GUIDE, PROJECT_BUILD, LOOK, SYSTEM, PUBLISH, SHELL_FILES };
