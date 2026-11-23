// 测试环境（应用这一侧）：model 层（编辑稿、改没改过、依赖表、JWT、引用、建议、选择框）、主进程调哪些 rf.py env 命令、
// 「让 AI 读项目，帮我填」交给聊天的话里没有任何值、会话里 rf.py env 的放行和禁止
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const root = path.join(__dirname, '..', 'app', 'src');
const main = path.join(root, 'main');
const M = require(path.join(root, 'renderer', 'scripts', 'env-model.js'));

const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
const jwt = (payload) => `${b64({ alg: 'HS256' })}.${b64(payload)}.sig`;
const REVEALED = {
  id: 'ENV-001', name: '本机', note: '都在本机', in_use: true, config: 'src/main/resources/application-local.yml',
  services: [{ name: '后端', command: 'mvn spring-boot:run', port: 8080, dir: '' }],
  deps: [
    { key: 'spring.datasource.url', name: '数据库', type: 'MySQL', address: 'jdbc:mysql://localhost:3306/b', host: 'localhost', port: 3306, from: 'config', check: { status: 'ok', detail: '' } },
    { key: 'pay.base-url', name: '支付服务', type: 'HTTP 接口', address: 'http://localhost:9090', host: 'localhost', port: 9090, from: 'user', replaces: true, check: { status: 'bad', detail: 'refused' } },
    { key: 'sms.url', name: 'sms', type: 'HTTP 接口', address: '${SMS_URL}', from: 'config', check: null, note: '占位符' },
  ],
  vars: [{ name: 'DB_PASSWORD', value: 'pw-1', secret: true, has_value: true }, { name: 'PAY_API', value: 'http://x', secret: false, has_value: true }],
  request: { items: [{ in: 'header', name: 'Authorization', value: 'Bearer {{user.jwt}}', secret: false }, { in: 'query', name: 'lang', value: 'zh-CN', secret: false }], before: '', after: '' },
  users: [{ id: 'U1', name: 'A', note: '普通用户', current: true, fields: [{ key: 'jwt', value: jwt({ sub: 'u_1', exp: 4102444800 }), secret: true }, { key: 'uid', value: 'u_1', secret: false }] }],
  proposal: { at: '2026-11-18T10:00:00', summary: '读了 pom.xml', items: [
    { k: 1, kind: 'service', name: '后端', command: './mvnw spring-boot:run', port: 8080, dir: '', why: 'pom.xml', state: 'pending' },
    { k: 2, kind: 'var', name: 'DB_PASSWORD', secret: true, why: '口令', state: 'pending' },
    { k: 3, kind: 'config', file: 'a.yml', state: 'ignored' }] },
};

test('draft: from the revealed settings, unchanged means clean; what is sent to rf.py env save', () => {
  const d = M.draftFrom(REVEALED);
  assert.equal(M.isDirty(d, REVEALED), false);
  assert.deepEqual(d.services[0], { name: '后端', command: 'mvn spring-boot:run', port: '8080', dir: '', auto_restart: true, accept: [] });
  assert.deepEqual(d.deps, [{ key: 'pay.base-url', name: '支付服务', type: 'HTTP 接口', address: 'http://localhost:9090' }]); // 只有手改的
  d.vars[1].value = 'http://y';
  d.users.push(M.blankRow('user'));
  assert.equal(M.isDirty(d, REVEALED), true);
  assert.deepEqual(M.changedSections(d, REVEALED), ['环境变量', '测试用户']);
  const p = M.payloadOf(d);
  assert.equal(p.deps[0].from, 'user');
  assert.equal('id' in p.users[1], false); // 新加的用户由程序给编号
  assert.equal(p.users[0].id, 'U1');
  assert.equal(p.vars[0].value, 'pw-1'); // 保密的值照样交给 rf.py（它存到本机）
});

test('dependency table: config rows, a user row replacing one, unsaved rows, counts and status pills', () => {
  const rows = M.depRows(REVEALED, M.draftFrom(REVEALED));
  assert.deepEqual(rows.map((r) => [r.key, r.from, r.edit]), [['spring.datasource.url', 'config', -1], ['sms.url', 'config', -1], ['pay.base-url', 'user', 0]]);
  assert.equal(rows[2].check.status, 'bad');
  assert.deepEqual(M.depCounts(rows), { ok: 1, bad: 1, none: 0, unchecked: 1 });
  const d = M.draftFrom(REVEALED);
  d.deps.push({ key: 'spring.datasource.url', name: '', type: '', address: 'jdbc:mysql://other:3306/b' });
  const again = M.depRows(REVEALED, d);
  assert.deepEqual(again.map((r) => r.key), ['sms.url', 'pay.base-url', 'spring.datasource.url']); // 被盖过的配置行不再列
  assert.equal(again[2].check, null); // 地址改了，上次的检查不算
  assert.equal(again[2].name, '数据库');
  assert.match(M.statusPill({ status: 'ok' }), /能连上/);
  assert.match(M.statusPill({ status: 'bad', detail: 'refused' }), /连不上.*|title="refused"/);
  assert.match(M.statusPill({ status: 'none' }), /没配/);
  assert.match(M.statusPill(null), /还没检查/);
  assert.match(M.scanLine({ at: '2026-11-18T10:05:00', kind: 'yaml', skipped: [{ line: 3, why: 'x' }], stale: true }), /11-18 10:05（YAML）；配置文件之后改过.*跳过 1 处/);
  assert.equal(M.scanLine({ error: '配置文件 a.yml 不在' }), '配置文件 a.yml 不在');
});

test('JWT: decoded payload, expiry, not a JWT, broken', () => {
  const now = Date.UTC(2026, 10, 18);
  const ok = M.jwtSummary(M.jwtInfo('Bearer ' + jwt({ sub: 'u_1', role: 'admin', exp: 4102444800, iat: 1 }), now));
  assert.deepEqual(ok.fields, ['sub=u_1', 'role=admin']);
  assert.equal(ok.state, 'ok');
  assert.match(ok.exp.text, /^有效到 2100-01-01|^有效到 2099-12-31/);
  const old = M.jwtSummary(M.jwtInfo(jwt({ sub: 'x', exp: 1000 }), now));
  assert.equal(old.state, 'bad');
  assert.match(old.exp.text, /^已过期（1970-01-01/);
  assert.equal(M.jwtSummary(M.jwtInfo(jwt({ sub: 'x' }), now)).exp.text, '没有过期时间');
  assert.equal(M.jwtInfo('u_1001'), null);
  assert.match(M.jwtInfo('abc.def.ghi').error, /解不出来/);
  assert.equal(M.looksJwt(jwt({})), true);
});

test('references in request values; proposal items; the select box', () => {
  const d = M.draftFrom(REVEALED);
  assert.deepEqual(M.refsOf('Bearer {{user.jwt}} {{DB_PASSWORD}}', d, d.users[0]), { refs: ['user.jwt', 'DB_PASSWORD'], missing: [] });
  assert.deepEqual(M.refsOf('{{user.aid}}-{{NOPE}}', d, d.users[0]).missing, ['user.aid', 'NOPE']);
  const items = M.proposalItems(REVEALED.proposal);
  assert.deepEqual(items.map((i) => [i.k, i.kind, i.text]), [[1, '启动命令', '后端：./mvnw spring-boot:run · 端口 8080'], [2, '环境变量', 'DB_PASSWORD（保密，值由你自己填）']]);
  const html = M.selectHtml({ use: 'ENV-002', envs: [{ id: 'ENV-001', name: '本机' }, { id: 'ENV-002', name: '测试服' }] });
  assert.match(html, /<option value="ENV-002" selected>测试服<\/option>/);
  assert.match(html, /data-envgo="1">设置</);
  assert.match(M.selectHtml({ use: null, envs: [] }), /<option value="" selected>还没有测试环境/);
  assert.match(M.selectHtml(null), /disabled/);
  assert.match(M.secretPill(true), /只存本机/);
  assert.match(M.secretPill(false), /进仓库/);
});

// ---------- 主进程 ----------
function loadMain(answers) {
  const calls = [];
  for (const k of Object.keys(require.cache)) if (k.startsWith(main)) delete require.cache[k];
  const backendPath = require.resolve(path.join(main, 'backend'));
  const real = require(backendPath);
  const rf = async (args, rootDir, agent, timeout, input) => {
    calls.push({ args, input, timeout });
    const made = answers[args.slice(0, 2).join(' ')];
    return made === undefined ? { ok: false, stdout: '', stderr: '不认识', timedOut: false } : { ok: !made.error, stdout: made.error ? '' : JSON.stringify(made), stderr: made.error || '', timedOut: false };
  };
  require.cache[backendPath].exports = Object.assign({}, real, { rf });
  const { state } = require(path.join(main, 'state'));
  state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-envui-'));
  return { E: require(path.join(main, 'envs')), state, calls };
}

test('main: which rf.py env commands it runs; the editor reads with reveal, saving goes through standard input', async () => {
  const masked = Object.assign({}, REVEALED, { vars: [{ name: 'DB_PASSWORD', value: null, secret: true, has_value: true }, { name: 'PAY_API', value: 'http://x', secret: false }] });
  const { E, calls } = loadMain({ 'env list': { use: 'ENV-001', envs: [], local: '/h/envs/p.json' }, 'env reveal': REVEALED, 'env save': REVEALED, 'env new': REVEALED, 'env copy': REVEALED,
    'env rm': { removed: true }, 'env use': { use: null }, 'env user': {}, 'env adopt': REVEALED, 'env ignore': REVEALED, 'env scan': {}, 'env check': {}, 'env resolve': {}, 'env show': masked });
  await E.list();
  await E.show({ id: 'ENV-001' });
  await E.save({ id: 'ENV-001', payload: { name: '本机' } });
  await E.act({ action: 'new', name: ' 测试服 ', note: '公司' });
  await E.act({ action: 'copy', id: 'ENV-001' });
  await E.act({ action: 'rm', id: 'ENV-001' });
  await E.act({ action: 'use', id: '' });
  await E.act({ action: 'use', id: 'ENV-002' });
  await E.act({ action: 'user', id: 'ENV-001', user: 'U2' });
  await E.act({ action: 'adopt', id: 'ENV-001', ks: [1, 3] });
  await E.act({ action: 'ignore', id: 'ENV-001' });
  await E.scan({ id: 'ENV-001' });
  await E.check({ id: 'ENV-001' });
  await E.resolve({ id: 'ENV-001', user: 'U1' });
  assert.deepEqual(calls.map((c) => c.args.join(' ')), ['env list --json', 'env reveal ENV-001 --json', 'env save ENV-001 --file - --json', 'env new 测试服 --note 公司 --json',
    'env copy ENV-001 --json', 'env rm ENV-001 --json', 'env use none --json', 'env use ENV-002 --json', 'env user ENV-001 U2 --json', 'env adopt ENV-001 1 3 --json',
    'env ignore ENV-001 --json', 'env scan ENV-001 --json', 'env check ENV-001 --json', 'env resolve ENV-001 --user U1 --json']);
  assert.deepEqual(JSON.parse(calls[2].input), { name: '本机' });
  assert.deepEqual(await E.act({ action: 'user', id: 'ENV-001', user: '' }), { error: '先保存这个测试用户。' });
  assert.deepEqual(await E.act({ action: 'save', id: 'ENV-001' }), { error: '不认识的操作。' });
  assert.deepEqual(await E.show({ id: '../x' }), { error: '测试环境的编号不对。' });
  // 让 AI 帮我填：用打码的 show 拼话，里面没有任何值；记下这个聊天可以用 env propose
  const made = await E.aiFill({ id: 'ENV-001' });
  assert.equal(calls[calls.length - 1].args.join(' '), 'env show ENV-001 --json');
  assert.ok(made.ok && made.sessionId);
  assert.match(made.say, /帮我填测试环境「本机」（ENV-001）/);
  assert.match(made.say, /env propose ENV-001 --file -/);
  assert.match(made.say, /环境变量 DB_PASSWORD（保密）、PAY_API/);
  assert.match(made.say, /不要运行 env new、copy、rename、rm、save、use、user、reveal、adopt、ignore/);
  assert.doesNotMatch(made.say, /pw-1|http:\/\/x|eyJ/);
  const { state } = require(path.join(main, 'state'));
  assert.deepEqual(state.envChats.get(made.sessionId), { project: state.project, env: 'ENV-001' });
});

test('main: the file watch notices settings, removed files and the local file (secrets, proposals)', async () => {
  const { E } = loadMain({});
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-envw-'));
  fs.mkdirSync(path.join(dir, '.relayforge', 'envs'), { recursive: true });
  const local = path.join(dir, 'local.json');
  const a = E.envsStamp(dir, local);
  fs.writeFileSync(path.join(dir, '.relayforge', 'envs', 'ENV-001.json'), '{}');
  const b = E.envsStamp(dir, local);
  assert.notEqual(a, b);
  fs.writeFileSync(local, '{}');
  const c = E.envsStamp(dir, local);
  assert.notEqual(b, c);
  fs.unlinkSync(path.join(dir, '.relayforge', 'envs', 'ENV-001.json'));
  assert.notEqual(E.envsStamp(dir, local), c);
});

test('sessions: rf.py env read commands for development and fix sessions and the fill-in chat; writing and plaintext denied everywhere', () => {
  require(path.join(main, 'state')).state.project = fs.mkdtempSync(path.join(os.tmpdir(), 'rf-envp-'));
  for (const k of Object.keys(require.cache)) if (k.startsWith(path.join(main, 'claude-settings'))) delete require.cache[k];
  const { hooks } = require(path.join(main, 'claude-settings'));
  const { BUNDLED, RF_HOME } = require(path.join(main, 'config'));
  const kinds = { building: hooks(null, false, false, true), fill: hooks(null, false, false, false, false, true), chat: hooks(null, false), testing: hooks(null, false, false, false, true),
    intake: hooks('/drafts/R-1', false, false, true, false, true), probe: hooks('/drafts/probe-I-1', true, false, true, false, true), merge: hooks(null, false, true) };
  for (const [name, text] of Object.entries(kinds)) {
    const { allow, deny } = JSON.parse(text).permissions;
    const reads = name === 'building' || name === 'fill';
    for (const rf of [BUNDLED, '.relayforge/bin/rf.py']) {
      for (const a of ['list', 'show', 'scan', 'check', 'propose']) assert.equal(allow.includes(`Bash(python3 ${rf} env ${a}:*)`), reads, `${name} ${a}`);
      for (const a of ['new', 'copy', 'rename', 'rm', 'save', 'use', 'user', 'reveal', 'adopt', 'ignore']) {
        assert.ok(deny.includes(`Bash(python3 ${rf} env ${a}:*)`), `${name} ${a} denied`);
        assert.ok(!allow.some((x) => x.includes(` env ${a}`)), `${name} ${a} not allowed`);
      }
      assert.ok(!allow.some((x) => x.includes(' env resolve')), `${name} resolve not pre-allowed`);
    }
    assert.ok(deny.includes(`Read(/${RF_HOME}/envs/**)`), `${name} cannot read the local secrets`);
  }
});

test('the session gets the fill-in rules when it is the env chat', () => {
  const src = fs.readFileSync(path.join(main, 'sessions.js'), 'utf8');
  assert.match(src, /state\.envChats\.has\(sessionId\)/);
  assert.match(src, /hooks\(drafts, probing, .*, building, testing, envFill, envFix, \{ role, place: tester \? root : null, split: splitEnabled, req: .*\}\)/);
});
