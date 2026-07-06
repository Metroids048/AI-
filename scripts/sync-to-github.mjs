import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REMOTE = 'https://github.com/Metroids048/AI-.git';
const BRANCH = 'main';

function git(args, { allowFail = false } = {}) {
  const result = spawnSync('git', args, {
    cwd: ROOT,
    encoding: 'utf8',
    env: { ...process.env, GIT_TERMINAL_PROMPT: '0' },
  });
  const out = `${result.stdout || ''}${result.stderr || ''}`.trim();
  if (result.status !== 0 && !allowFail) {
    throw new Error(out || `git ${args.join(' ')} 失败`);
  }
  return { ok: result.status === 0, out };
}

function setup() {
  const gitDir = path.join(ROOT, '.git');
  if (!fs.existsSync(gitDir) || fs.readdirSync(gitDir).length === 0) {
    if (fs.existsSync(gitDir)) fs.rmSync(gitDir, { recursive: true, force: true });
    git(['init']);
    git(['branch', '-M', BRANCH]);
  }
  const currentRemote = git(['remote', 'get-url', 'origin'], { allowFail: true }).out;
  if (!currentRemote) {
    git(['remote', 'add', 'origin', REMOTE]);
  } else if (currentRemote !== REMOTE) {
    git(['remote', 'set-url', 'origin', REMOTE]);
  }
}

function hasLocalChanges() {
  return Boolean(git(['status', '--porcelain']).out);
}

function hasUnpushedCommits() {
  const upstream = git(['rev-parse', '--abbrev-ref', `${BRANCH}@{upstream}`], { allowFail: true }).out;
  if (!upstream) return true;
  const count = git(['rev-list', '--count', `${BRANCH}@{upstream}..HEAD`], { allowFail: true }).out;
  return count !== '0';
}

function commitAll() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const message = `sync ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}`;
  git(['commit', '-m', message]);
}

function push() {
  git(['branch', '-M', BRANCH]);
  let result = git(['push', '-u', 'origin', BRANCH], { allowFail: true });
  if (result.ok) return;

  git(['pull', '--rebase', 'origin', BRANCH], { allowFail: true });
  result = git(['push', '-u', 'origin', BRANCH]);
  if (!result.ok) throw new Error(result.out || '推送失败');
}

try {
  console.log('正在上传到 GitHub...');
  setup();
  git(['add', '-A']);

  if (hasLocalChanges()) {
    console.log('发现变更，正在提交...');
    commitAll();
  } else if (!hasUnpushedCommits()) {
    console.log('没有新变更，GitHub 已是最新。');
    process.exit(0);
  }

  console.log('正在推送...');
  push();
  console.log('上传成功 -> https://github.com/Metroids048/AI-');
} catch (error) {
  console.error('\n上传失败:', error.message || error);
  console.error('\n若是第一次在这台电脑上传，先在终端执行一次：');
  console.error('  git push -u origin main');
  console.error('按提示登录 GitHub 后，再双击「上传.cmd」即可。');
  process.exit(1);
}
