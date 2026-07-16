import {spawnSync} from "node:child_process";

const index = process.argv.indexOf("--seed");
const seed = process.env.LA_TEST_SEED || (index >= 0 ? process.argv[index + 1] : "") || `study-${Date.now()}`;
const result = spawnSync(process.execPath, ["--test", "tests/study-workspace.test.mjs"], {
  cwd: new URL("..", import.meta.url), env: {...process.env, LA_TEST_SEED: seed}, encoding: "utf8", timeout: 120_000,
});
process.stdout.write(result.stdout); process.stderr.write(result.stderr);
if (result.status !== 0) process.stderr.write(`Replay: LA_TEST_SEED=${seed} npm run test:random\n`);
process.exit(result.status ?? 1);
