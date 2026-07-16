import {spawnSync} from "node:child_process";

const seeds = Array.from({length: 20}, (_, index) => `study-regression-${String(index + 1).padStart(2, "0")}`);
for (const seed of seeds) {
  const result = spawnSync(process.execPath, ["--test", "tests/study-workspace.test.mjs"], {
    cwd: new URL("..", import.meta.url),
    env: {...process.env, LA_TEST_SEED: seed},
    encoding: "utf8",
    timeout: 120_000,
  });
  if (result.status !== 0) {
    process.stderr.write(`Randomized study test failed. Replay with LA_TEST_SEED=${seed} npm run test:random\n`);
    process.stderr.write(result.stdout); process.stderr.write(result.stderr);
    process.exit(result.status ?? 1);
  }
  process.stdout.write(`ok ${seed}\n`);
}
