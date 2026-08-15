import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { Codex } from "@openai/codex-sdk";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const files = {
  goal: path.join(root, "CODEX_STRATEGY_GOAL.md"),
  ledger: path.join(root, "LOOP_LEDGER.json"),
  status: path.join(root, "FINAL_STATUS.json"),
  resume: path.join(root, "FINAL_RESUME_STATE.json"),
  lock: path.join(root, ".codex-strategy-supervisor.lock"),
};

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

function writeJson(file, value) {
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  fs.renameSync(temporary, file);
}

function requireStateFiles() {
  for (const file of [files.goal, files.ledger, files.status, files.resume]) {
    if (!fs.existsSync(file)) throw new Error(`Required state file missing: ${file}`);
  }
}

function continuationPrompt() {
  return [
    "Read CODEX_STRATEGY_GOAL.md, FINAL_RESUME_STATE.json, LOOP_LEDGER.json, FINAL_STATUS.json, and current repository/runtime.",
    "The preceding turn response is not delivery. Execute LOOP_LEDGER.json.next_machine_action now.",
    "Update LOOP_LEDGER.json and FINAL_STATUS.json atomically before finishing this turn.",
    "If FINAL_STATUS.json.success is false, leave the next concrete machine action and do not re-plan from scratch.",
  ].join("\n");
}

function acquireLock() {
  try {
    fs.writeFileSync(files.lock, `${process.pid}\n${new Date().toISOString()}\n`, { flag: "wx" });
  } catch {
    throw new Error(`Supervisor lock exists: ${files.lock}`);
  }
}

function releaseLock() {
  if (fs.existsSync(files.lock)) fs.rmSync(files.lock, { force: true });
}

async function main() {
  requireStateFiles();
  acquireLock();
  const cleanup = () => releaseLock();
  process.once("SIGINT", cleanup);
  process.once("SIGTERM", cleanup);

  try {
    const codex = new Codex();
    let ledger = readJson(files.ledger);
    const thread = ledger.supervisor.thread_id
      ? codex.resumeThread(ledger.supervisor.thread_id)
      : codex.startThread();
    let failures = 0;

    while (!readJson(files.status).success) {
      try {
        const result = await thread.run(continuationPrompt());
        ledger = readJson(files.ledger);
        ledger.supervisor.thread_id = thread.id;
        ledger.supervisor.turn_count = Number(ledger.supervisor.turn_count || 0) + 1;
        ledger.supervisor.last_turn_at = new Date().toISOString();
        ledger.supervisor.last_response = result.finalResponse;
        writeJson(files.ledger, ledger);
        failures = 0;
      } catch (error) {
        failures += 1;
        console.error(error);
        await sleep(Math.min(60_000, 5_000 * 2 ** Math.min(failures, 4)));
        continue;
      }
      await sleep(3_000);
    }
  } finally {
    releaseLock();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
