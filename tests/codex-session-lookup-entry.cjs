const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

exports.run = function ({ id, record, transcripts }) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-codex-lookup-'));
  const configPath = require.resolve('../app/src/main/config');
  const modulePath = require.resolve('../app/src/main/codex-sessions');
  const savedEnv = { RELAYFORGE_HOME: process.env.RELAYFORGE_HOME, CODEX_HOME: process.env.CODEX_HOME };
  try {
    process.env.RELAYFORGE_HOME = home;
    process.env.CODEX_HOME = home;
    const sessions = path.join(home, 'sessions');
    fs.mkdirSync(sessions);
    for (const { name, lines, padding } of transcripts) {
      const [header, ...messages] = lines;
      const body = padding ? [{ type: 'turn_context', payload: ' '.repeat(padding) }, ...messages] : messages;
      fs.writeFileSync(path.join(sessions, name), [header, ...body].map((line) => JSON.stringify(line)).join('\n') + '\n');
    }
    const { transcript, ...saved } = record;
    if (transcript) saved.transcript = path.join(sessions, transcript);
    fs.writeFileSync(path.join(home, 'codex-sessions.json'), JSON.stringify({ [id]: saved }));
    delete require.cache[configPath];
    delete require.cache[modulePath];
    const lookup = require(modulePath);
    const found = lookup.find(id);
    return { transcript: found && path.basename(found), nativeId: lookup.nativeId(id) || null };
  } finally {
    delete require.cache[modulePath];
    delete require.cache[configPath];
    for (const [key, value] of Object.entries(savedEnv)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(home, { recursive: true, force: true });
  }
};
