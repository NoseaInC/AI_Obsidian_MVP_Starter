import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

test("API client only accepts localhost", () => {
  const source = readFileSync(new URL("../src/api.ts", import.meta.url), "utf8");
  assert.match(source, /127\.0\.0\.1/);
  assert.match(source, /Agent URL must be localhost/);
  assert.match(source, /protocol_version/);
  assert.match(source, /Agent health protocol mismatch/);
  assert.doesNotMatch(source, /API[_ -]?KEY/i);
});

test("plugin exposes required commands and explicit confirmation", () => {
  const source = readFileSync(new URL("../main.ts", import.meta.url), "utf8");
  for (const id of ["import-pdf", "view-jobs", "review", "expand-idea", "today-learning", "next-week"]) assert.match(source, new RegExp(`id: "${id}"`));
  assert.match(source, /window\.confirm/);
  assert.match(source, /修改后接受/);
  assert.match(source, /ContentModal/);
  assert.match(source, /服务在线，但面板数据加载失败/);
  assert.match(source, /leaf\.view\.refresh/);
});
