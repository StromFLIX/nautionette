#!/usr/bin/env node
/**
 * The whole contract of a Pi container.
 *
 * In:  AGENT_JOB (base64 JSON) or AGENT_JOB_FILE (large jobs with images/history).
 * Out: NDJSON events on stdout, one JSON object per line, ending in `result`.
 *
 * Nothing is remembered between runs: the container starts, works and exits.
 */
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, readFileSync, unlinkSync, writeFileSync } from "node:fs";
import { prepareProjects, projectEnvironment } from "./project-git.mjs";
import { contextUsage } from "./context-usage.mjs";
import { createChatControl, listenForChatControl } from "./chat-control.mjs";

const OUT = process.stdout;

function emit(event) {
  OUT.write(JSON.stringify(event) + "\n");
}

function log(...args) {
  // stdout is the protocol; anything human goes to stderr.
  console.error("[agent-run]", ...args);
}

function readJob() {
  if (process.env.AGENT_JOB_FILE) {
    const job = JSON.parse(readFileSync(process.env.AGENT_JOB_FILE, "utf8"));
    unlinkSync(process.env.AGENT_JOB_FILE);
    return job;
  }
  const raw = process.env.AGENT_JOB;
  if (!raw) throw new Error("AGENT_JOB is not set");
  return JSON.parse(Buffer.from(raw, "base64").toString("utf8"));
}

function renderPrompt(job) {
  const parts = [];
  let imageNumber = 0;
  const references = (images = []) => images.map(() => `[Attached image ${++imageNumber}]`).join("\n");
  if (job.history?.length) {
    parts.push("Conversation so far:");
    for (const message of job.history) {
      const who = message.role === "assistant" ? "Assistant" : "User";
      parts.push(`${who}: ${message.content}`);
      if (message.images?.length) parts.push(references(message.images));
    }
    parts.push("---");
  }
  parts.push(job.prompt || (job.images?.length ? "Please examine the attached image(s)." : ""));
  if (job.images?.length) parts.push(references(job.images));
  if (job.output_schema) {
    parts.push(
      "",
      "Reply with a single JSON object and nothing else. No prose, no code fence.",
      "It must satisfy this JSON Schema:",
      JSON.stringify(job.output_schema, null, 2),
    );
  }
  return parts.join("\n");
}

function extractJson(text) {
  if (!text) return null;
  // Only accept an object at the END of the answer (optionally fenced).
  // An earlier example/placeholder must never outrank a later final answer.
  // If that final answer is malformed, fail rather than use stale draft JSON.
  const trimmed = text.trim().replace(/\s*```$/, "").trim();
  for (let start = trimmed.indexOf("{"); start !== -1; start = trimmed.indexOf("{", start + 1)) {
    try {
      return JSON.parse(trimmed.slice(start));
    } catch {
      /* This opening brace is not the start of a complete final object. */
    }
  }
  return null;
}

function checkSchema(value, schema) {
  const problems = [];
  if (schema?.type === "object" && (typeof value !== "object" || value === null || Array.isArray(value))) {
    problems.push("expected a JSON object");
    return problems;
  }
  for (const key of schema?.required ?? []) {
    if (!(key in value)) problems.push(`missing required key '${key}'`);
  }
  return problems;
}

// A tool result is a Pi ToolResult ({ content: [{type:'text'}], details }); the UI
// only ever shows it, so flatten it to text here and cap what crosses the wire.
const MAX_TOOL_RESULT_CHARS = 4000;

function toolResultText(result) {
  if (result == null) return "";
  const text = typeof result === "string"
    ? result
    : (Array.isArray(result.content) ? result.content : [])
        .filter((part) => part?.type === "text" && typeof part.text === "string")
        .map((part) => part.text)
        .join("\n") || JSON.stringify(result);
  return text.length > MAX_TOOL_RESULT_CHARS
    ? `${text.slice(0, MAX_TOOL_RESULT_CHARS)}\n… ${text.length - MAX_TOOL_RESULT_CHARS} more characters`
    : text;
}

function explain(error) {
  // The one failure everybody hits first deserves a sentence, not a status code.
  if (/401/.test(error) && /auth/i.test(error)) {
    return (
      "the gateway has no model provider key: set OPENROUTER_API_KEY and restart agentgateway " +
      `(upstream said: ${error.slice(0, 200)})`
    );
  }
  return error;
}

async function main() {
  const job = readJob();
  const mode = job.mode ?? "interactive";
  const model = job.model || process.env.AGENT_MODEL || "openai/gpt-4o-mini";
  const workspace = "/workspace";

  mkdirSync(workspace, { recursive: true });
  if (job.project_ids?.length && existsSync("/root/.pi/agent")) {
    cpSync("/root/.pi/agent", `${workspace}/.pi-agent`, { recursive: true });
  }
  // Whatever the agent set ships (AGENTS.md and friends) becomes the context for
  // this call. The container is new every time, so this is the only way in.
  if (existsSync("/workspace-defaults")) {
    cpSync("/workspace-defaults", workspace, { recursive: true });
  }
  writeFileSync(`${workspace}/JOB.json`, JSON.stringify({ ...job, history: undefined, images: undefined, project_credentials: undefined }, null, 2));
  if (job.project_ids?.length) {
    emit({ type: "status", state: "projects", message: "Preparing this chat's project worktrees" });
    prepareProjects(job);
  }

  const prompt = renderPrompt(job);
  const interactive = Boolean(job.chat_id);
  const args = ["--mode", interactive ? "rpc" : "json", "--no-session", "--provider", "nautionette",
                "--model", model, "--approve"];
  if (job.system_prompt) args.push("--append-system-prompt", job.system_prompt);
  if (!interactive) args.push("--", prompt);

  const child = spawn("pi", args, {
    cwd: workspace,
    env: {
      ...process.env,
      ...projectEnvironment(job),
      AGENT_MODEL: model,
      NAUTIONETTE_MODEL_IMAGES: job.supports_images === false ? "false" : "true",
      NAUTIONETTE_MODEL_API: job.model_api || "",
      NAUTIONETTE_MODEL_REASONING: JSON.stringify(job.model_reasoning || {}),
      NAUTIONETTE_REASONING_EFFORT: job.reasoning_effort ?? "",
      NAUTIONETTE_MODE: mode,
      NAUTIONETTE_INTERNET_STATUS: job.chat_id ? (job.internet_status || "blocked") : "",
      // JSON preserves null (all) versus [] (none) and tool names containing commas.
      NAUTIONETTE_TOOLS_JSON: JSON.stringify(job.tools ?? null),
      // Compatibility for older/custom agent sets. New bridges use the lossless value above.
      NAUTIONETTE_TOOLS: Array.isArray(job.tools) ? job.tools.join(",") : "",
    },
    stdio: [interactive ? "pipe" : "ignore", "pipe", "pipe"],
  });

  const send = (command) => child.stdin.write(JSON.stringify(command) + "\n");
  const control = interactive ? createChatControl({ send, emit }) : null;
  const controlServer = control ? listenForChatControl(control) : null;
  child.stdin?.on("error", (error) => log("pi input closed:", error.message));

  let finalText = "";
  let streamed = "";
  let stderr = "";
  let buffer = "";
  let runError = "";
  let context = null;
  let usage = null;
  const result = (event) => emit({ type: "result", ...event, context, usage });

  child.stdout.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    let index;
    while ((index = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, index).trim();
      buffer = buffer.slice(index + 1);
      if (!line) continue;
      let event;
      try {
        event = JSON.parse(line);
      } catch {
        continue;
      }
      translate(event);
    }
  });

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
    if (stderr.length > 8000) stderr = stderr.slice(-8000);
  });

  function translate(event) {
    control?.receive(event);
    switch (event.type) {
      case "response":
        if (event.id === "initial" && !event.success) {
          runError = event.error || "Pi rejected the initial prompt";
          emit({ type: "error", message: runError });
          child.kill();
        }
        break;
      case "session":
        emit({ type: "session", id: event.id });
        break;
      case "message_update": {
        const inner = event.assistantMessageEvent;
        if (inner?.type === "text_delta" && inner.delta) {
          streamed += inner.delta;
          emit({ type: "delta", text: inner.delta });
        } else if (inner?.type === "thinking_delta" && inner.delta) {
          emit({ type: "thinking", text: inner.delta });
        }
        break;
      }
      case "tool_execution_start":
        emit({ type: "tool", id: event.toolCallId, name: event.toolName, args: event.args });
        break;
      case "tool_execution_end":
        emit({
          type: "tool_done",
          id: event.toolCallId,
          name: event.toolName,
          error: Boolean(event.isError),
          result: toolResultText(event.result),
        });
        break;
      case "message_end": {
        const message = event.message;
        if (message?.role === "assistant") {
          context = contextUsage(message, model);
          usage = context ? message.usage : null;
          emit({ type: "usage", context });
          if (message.stopReason === "error" && message.errorMessage) {
            runError = explain(message.errorMessage);
            emit({ type: "error", message: runError });
          }
          const text = (message.content ?? [])
            .filter((part) => part.type === "text")
            .map((part) => part.text)
            .join("");
          if (text.trim()) finalText = text;
        }
        break;
      }
      case "agent_end":
        emit({ type: "agent_end" });
        if (interactive) child.kill();
        break;
      default:
        break;
    }
  }

  const completion = new Promise((resolve) => {
    child.on("error", (error) => {
      log("failed to start pi:", error.message);
      stderr += `\n${error.message}`;
      resolve(127);
    });
    child.on("close", resolve);
  });
  if (interactive) {
    send({ type: "set_steering_mode", mode: "one-at-a-time" });
    const images = [...(job.history || []).flatMap((message) => message.images || []), ...(job.images || [])];
    send({ id: "initial", type: "prompt", message: prompt, ...(images.length ? { images } : {}) });
  }
  const code = await completion;
  control?.close();
  controlServer?.close();

  const text = (finalText || streamed).trim();

  if (!text && (runError || code !== 0)) {
    result({
      ok: false,
      text: "",
      output: null,
      error: runError || `pi exited with ${code}: ${stderr.trim().slice(-1200) || "no output"}`,
    });
    return;
  }

  if (job.output_schema) {
    const parsed = extractJson(text);
    if (!parsed) {
      result({
        ok: false,
        text,
        output: null,
        error: "the model returned text where a structured object was declared",
      });
      return;
    }
    const problems = checkSchema(parsed, job.output_schema);
    if (problems.length) {
      result({ ok: false, text, output: parsed, error: problems.join("; ") });
      return;
    }
    result({ ok: true, text, output: parsed });
    return;
  }

  result({ ok: true, text, output: null });
}

main().catch((error) => {
  emit({ type: "result", ok: false, text: "", output: null, error: String(error?.message ?? error) });
  process.exitCode = 1;
});
