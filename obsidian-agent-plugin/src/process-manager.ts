import {ChildProcessWithoutNullStreams, spawn} from "child_process";
import {randomBytes} from "crypto";
import {createWriteStream, existsSync, mkdirSync, WriteStream} from "fs";
import {join} from "path";
import {requestUrl} from "obsidian";

export interface RuntimeSettings {
  port: number;
  pythonPath: string;
  autoStart: boolean;
  stopOnUnload: boolean;
}

export type RuntimeStatus = "stopped" | "starting" | "online" | "error";

export class AgentProcessManager {
  readonly sessionToken = randomBytes(32).toString("base64url");
  readonly runtimeId = randomBytes(16).toString("hex");
  status: RuntimeStatus = "stopped";
  lastError = "";
  private child: ChildProcessWithoutNullStreams | null = null;
  private log: WriteStream | null = null;

  constructor(private vaultPath: string, private settings: RuntimeSettings, private changed: () => void) {}
  get baseUrl() { return `http://127.0.0.1:${this.settings.port}`; }
  get pid() { return this.child?.pid ?? null; }
  get pythonPath() {
    if (this.settings.pythonPath.trim()) return this.settings.pythonPath.trim();
    const bundled = join(this.vaultPath, "00-System", "Scripts", ".venv", "bin", "python");
    return existsSync(bundled) ? bundled : "python3";
  }

  private setStatus(status: RuntimeStatus, error = "") { this.status = status; this.lastError = error; this.changed(); }

  private async healthPayload(): Promise<any | null> {
    try {
      const response = await requestUrl({url: `${this.baseUrl}/health`, method: "GET", throw: false});
      return response.status === 200 && response.json?.ok === true && response.json?.service === "obsidian-learning-agent" && response.json?.protocol_version === 1 ? response.json : null;
    } catch { return null; }
  }

  async isHealthy(): Promise<boolean> {
    const health = await this.healthPayload();
    return health?.runtime_id === this.runtimeId;
  }

  async ensureRunning(force = false): Promise<void> {
    const existing = await this.healthPayload();
    if (existing?.runtime_id === this.runtimeId) { this.setStatus("online"); return; }
    if (existing) {
      if (existing.vault !== this.vaultPath || !Number.isInteger(existing.pid) || existing.pid <= 1) {
        this.setStatus("error", "PortConflict：端口已被另一个 Agent 占用"); return;
      }
      try { process.kill(existing.pid, "SIGTERM"); }
      catch (error: any) { this.setStatus("error", `无法停止旧 Agent：${error.message}`); return; }
      for (let attempt = 0; attempt < 20 && await this.healthPayload(); attempt++) await new Promise(resolve => window.setTimeout(resolve, 100));
      if (await this.healthPayload()) { this.setStatus("error", "旧 Agent 未能退出，请从诊断页重试"); return; }
    }
    if ((!this.settings.autoStart && !force) || this.child) return;
    this.setStatus("starting");
    const logDir = join(this.vaultPath, "90-Local-Only", "AgentLogs");
    mkdirSync(logDir, {recursive: true});
    this.log = createWriteStream(join(logDir, "plugin-runtime.log"), {flags: "a"});
    this.child = spawn(this.pythonPath, ["-m", "agent.api.server", "--vault", this.vaultPath, "--port", String(this.settings.port)], {
      cwd: this.vaultPath,
      env: {...process.env, OBSIDIAN_AGENT_SESSION_TOKEN: this.sessionToken, OBSIDIAN_AGENT_RUNTIME_ID: this.runtimeId, PYTHONUNBUFFERED: "1"},
      stdio: "pipe",
    });
    this.child.stdout.pipe(this.log, {end: false});
    this.child.stderr.pipe(this.log, {end: false});
    this.child.once("error", error => { this.child = null; this.setStatus("error", error.message); });
    this.child.once("exit", (code, signal) => {
      this.child = null;
      if (this.status !== "stopped") this.setStatus("error", `Agent 已退出（code=${code ?? "-"}, signal=${signal ?? "-"}）`);
    });
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise(resolve => window.setTimeout(resolve, 200));
      if (await this.isHealthy()) { this.setStatus("online"); return; }
      if (!this.child) return;
    }
    this.setStatus("error", "Agent 启动超时，请查看本地运行日志");
  }

  async restart(): Promise<void> { await this.stop(); await this.ensureRunning(true); }

  async activateRuntimeUpgrade(request: Record<string, unknown>): Promise<Record<string, unknown>> {
    const project = String(request.project ?? "");
    if (!project || project !== this.vaultPath) throw new Error("runtime_upgrade_project_mismatch");
    await this.runFixedRepositoryScript("check.sh", 15 * 60_000);
    await this.runFixedRepositoryScript("install-plugin.sh", 15 * 60_000);
    await this.restart();
    if (!await this.isHealthy()) throw new Error("runtime_upgrade_health_check_failed");
    return {
      installed: true,
      restarted: true,
      healthy: true,
      mergedHead: String(request.mergedHead ?? ""),
    };
  }

  async stop(): Promise<void> {
    const child = this.child;
    this.child = null;
    this.setStatus("stopped");
    if (child && !child.killed) {
      child.kill("SIGTERM");
      await new Promise(resolve => window.setTimeout(resolve, 300));
      if (child.exitCode === null) child.kill("SIGKILL");
    }
    this.log?.end(); this.log = null;
  }

  private async runFixedRepositoryScript(name: "check.sh" | "install-plugin.sh", timeoutMs: number): Promise<void> {
    const script = join(this.vaultPath, "scripts", name);
    if (!existsSync(script)) throw new Error(`runtime_upgrade_script_missing:${name}`);
    const environment = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !/(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)/i.test(key)),
    ) as NodeJS.ProcessEnv;
    await new Promise<void>((resolve, reject) => {
      const child = spawn("/bin/zsh", [script], {
        cwd: this.vaultPath,
        env: environment,
        stdio: "ignore",
      });
      const timer = window.setTimeout(() => {
        child.kill("SIGKILL");
        reject(new Error(`runtime_upgrade_timeout:${name}`));
      }, timeoutMs);
      child.once("error", error => {
        window.clearTimeout(timer);
        reject(new Error(`runtime_upgrade_spawn_failed:${name}:${error.message}`));
      });
      child.once("exit", code => {
        window.clearTimeout(timer);
        if (code === 0) resolve();
        else reject(new Error(`runtime_upgrade_script_failed:${name}`));
      });
    });
  }
}
