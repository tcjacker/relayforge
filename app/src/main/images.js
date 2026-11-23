// 图片：Claude Code 认的是文件路径，所以粘贴或拖进来的图片先存成文件。
const { dialog } = require('electron');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { state } = require('./state');

const IMAGE_TYPES = ['png', 'jpg', 'jpeg', 'gif', 'webp'];

async function pickImages() {
  const pick = await dialog.showOpenDialog(state.win, { title: '选择图片', properties: ['openFile', 'multiSelections'], filters: [{ name: '图片', extensions: IMAGE_TYPES }] });
  return pick.canceled ? [] : pick.filePaths;
}

function saveImage({ name, bytes }) {
  const ext = (path.extname(name || '').slice(1) || 'png').toLowerCase();
  if (!IMAGE_TYPES.includes(ext)) return null;
  const dir = path.join(os.tmpdir(), 'relayforge-images');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${Date.now()}-${crypto.randomBytes(3).toString('hex')}.${ext}`);
  fs.writeFileSync(file, Buffer.from(bytes));
  return file;
}

module.exports = { pickImages, saveImage };
