#!/usr/bin/env node
// evo-hook-drain — hot-path hook invoked by host plugins (Claude Code, Codex).
//
// Reads session_id from stdin's JSON payload (host hook contract), then does
// two stat checks; exits fast when there's nothing to deliver. Hands off to
// `evo-drain` (Python console_script) only when the marker says there's
// something to drain.
//
// Cross-platform (Linux / macOS / Windows). Invoked via exec form from
// hooks.json: `{"command": "node", "args": ["${CLAUDE_PLUGIN_ROOT}/bin/evo-hook-drain.js"]}`.
// node.exe ships signed and trusted on Windows so no SmartScreen / AV
// false-positives the way a custom-compiled .exe would hit.
//
// See notes/cross-host-inject-design.md.

"use strict"

const fs = require("fs")
const os = require("os")
const path = require("path")
const child_process = require("child_process")

const OK_EMPTY = "{}"
const SID_RE = /"session_id"\s*:\s*"([^"]+)"/
const EVENT_RE = /"hook_event_name"\s*:\s*"([^"]+)"/
const VERSION_RE = /"version"\s*:\s*"([^"]+)"/

function emitOK() {
  process.stdout.write(OK_EMPTY)
  process.exit(0)
}

function readStdinSync() {
  if (process.stdin.isTTY) return ""
  try {
    // Node 12+: fs.readFileSync on stdin fd works cross-platform.
    return fs.readFileSync(0, "utf8")
  } catch (_e) {
    return ""
  }
}

function findSessionID(stdinBuf) {
  const m = SID_RE.exec(stdinBuf)
  if (m) return m[1]
  const envVars = [
    "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID",
    "HERMES_SESSION_ID", "OPENCODE_SESSION_ID",
  ]
  for (const k of envVars) {
    if (process.env[k]) return process.env[k]
  }
  return ""
}

function findEvoRunDir() {
  if (process.env.EVO_RUN_DIR) return process.env.EVO_RUN_DIR
  let cwd = process.cwd()
  while (true) {
    const evoDir = path.join(cwd, ".evo")
    if (fs.existsSync(evoDir) && fs.statSync(evoDir).isDirectory()) {
      const entries = fs.readdirSync(evoDir)
      const runs = entries
        .filter((e) => e.startsWith("run_"))
        .map((e) => path.join(evoDir, e))
        .filter((p) => fs.statSync(p).isDirectory())
        .sort()
      return runs.length > 0 ? runs[runs.length - 1] : null
    }
    const parent = path.dirname(cwd)
    if (parent === cwd) return null
    cwd = parent
  }
}

function detectHostFromStdin(stdinBuf) {
  // Cross-platform path separators in the JSON payload (forward slash on
  // Unix, backslash on Windows when host serialises a workspace path).
  if (stdinBuf.includes(".codex/") || stdinBuf.includes("\\.codex\\")) return "codex"
  if (stdinBuf.includes(".hermes/") || stdinBuf.includes("\\.hermes\\")) return "hermes"
  if (stdinBuf.includes(".opencode/") || stdinBuf.includes("\\.opencode\\")) return "opencode"
  return "claude-code"
}

function registerSession(runDir, sid, host) {
  const sessionsDir = path.join(runDir, "inject", "sessions")
  fs.mkdirSync(sessionsDir, { recursive: true })
  const now = new Date().toISOString().replace(/\.\d{3}Z$/, "Z")
  const payload = {
    schema_version: 1,
    session_id: sid,
    host,
    pid: process.pid,
    registered_at: now,
    last_seen_at: now,
    exp_id: null,
    parent_session_id: null,
  }
  fs.writeFileSync(path.join(sessionsDir, `${sid}.json`), JSON.stringify(payload))
}

function readVersion(manifest) {
  try {
    const text = fs.readFileSync(manifest, "utf8")
    const m = VERSION_RE.exec(text)
    return m ? m[1] : null
  } catch (_e) {
    return null
  }
}

// Cross-platform `which`: probe PATH (and PATHEXT on Windows).
function whichSync(cmd) {
  const pathSep = process.platform === "win32" ? ";" : ":"
  const exts = process.platform === "win32"
    ? (process.env.PATHEXT || ".COM;.EXE;.BAT;.CMD").split(";")
    : [""]
  const pathDirs = (process.env.PATH || "").split(pathSep).filter(Boolean)
  for (const dir of pathDirs) {
    for (const ext of exts) {
      const full = path.join(dir, cmd + ext)
      try {
        const stat = fs.statSync(full)
        if (stat.isFile()) return full
      } catch (_e) {
        // continue
      }
    }
  }
  return null
}

function sessionStartDriftChecks(pluginRoot) {
  const cacheManifest = path.join(pluginRoot, ".claude-plugin", "plugin.json")
  let mktManifest = null

  const home = os.homedir()
  // Detect host by where the plugin was installed. Match both forward and
  // backslash variants because Windows path-normalises both directions.
  const pr = pluginRoot.replace(/\\/g, "/")
  if (pr.includes("/.claude/plugins/cache/")) {
    mktManifest = path.join(home, ".claude", "plugins", "marketplaces",
      "evo-hq-evo", "plugins", "evo", ".claude-plugin", "plugin.json")
  } else if (pr.includes("/.codex/plugins/cache/")) {
    mktManifest = path.join(home, ".codex", ".tmp", "marketplaces",
      "evo-hq", "plugins", "evo", ".claude-plugin", "plugin.json")
  }

  if (mktManifest && fs.existsSync(mktManifest) && fs.existsSync(cacheManifest)) {
    const cacheVer = readVersion(cacheManifest)
    const mktVer = readVersion(mktManifest)
    if (cacheVer && mktVer && cacheVer !== mktVer) {
      process.stderr.write(
        `evo: plugin cache is stale (running ${cacheVer}, marketplace has ${mktVer}). ` +
        `Run: evo update --force\n`
      )
    }
  }

  if (!whichSync("evo-drain")) {
    process.stderr.write(
      "evo: install evo-hq-cli to enable mid-run inject " +
      "(uv tool install evo-hq-cli)\n"
    )
  }
}

function handoffToDrain(runDir, sid, stdinBuf) {
  const drain = whichSync("evo-drain")
  if (!drain) {
    process.stderr.write(
      "evo-hook-drain: install evo-hq-cli to enable drain — " +
      "'uv tool install evo-hq-cli' or 'pipx install evo-hq-cli'\n"
    )
    process.stdout.write(OK_EMPTY)
    process.exit(1)
  }
  // spawnSync inherits stdout/stderr, forwards stdin buffer, returns
  // exit code. Cross-platform — no shell, no quoting issues.
  const r = child_process.spawnSync(drain,
    ["--run-dir", runDir, "--session", sid],
    { input: stdinBuf, stdio: ["pipe", "inherit", "inherit"] }
  )
  process.exit(r.status ?? 1)
}

function main() {
  const stdinBuf = readStdinSync()

  const sid = findSessionID(stdinBuf)
  if (!sid) emitOK()

  const runDir = findEvoRunDir()
  if (!runDir) emitOK()

  const eventMatch = EVENT_RE.exec(stdinBuf)
  const hookEvent = eventMatch ? eventMatch[1] : ""

  const sessionsFile = path.join(runDir, "inject", "sessions", `${sid}.json`)

  if (hookEvent === "SessionStart") {
    if (!fs.existsSync(sessionsFile)) {
      registerSession(runDir, sid, detectHostFromStdin(stdinBuf))
    }
    const pluginRoot = path.resolve(path.dirname(__filename), "..")
    sessionStartDriftChecks(pluginRoot)
  }

  if (!fs.existsSync(sessionsFile)) emitOK()

  if (hookEvent !== "SessionStart") {
    const marker = path.join(runDir, "inject", "markers", `${sid}.flag`)
    if (!fs.existsSync(marker)) emitOK()
  }

  handoffToDrain(runDir, sid, stdinBuf)
}

main()
