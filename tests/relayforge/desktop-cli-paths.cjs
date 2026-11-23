const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { locateCli } = require('../../app/src/main/cli-paths');

function run({ agent, platform, paths, applications, executables }) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'relayforge-cli-paths-'));
  try {
    for (const candidate of executables) {
      const file = path.join(root, candidate);
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, '', { mode: 0o700 });
    }
    const found = locateCli(agent, agent, {
      home: root, platform,
      applications: applications.map((dir) => path.join(root, dir)),
      paths: paths.map((dir) => path.join(root, dir)),
    });
    return { file: found.target ? path.relative(root, found.file) : null, desktop: found.desktop };
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
}

module.exports = { run };
