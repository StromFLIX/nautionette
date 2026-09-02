#!/usr/bin/env node
/**
 * The whole contract of a Pi container.
 *
 * In:  AGENT_JOB, a base64 JSON job (prompt, history, optional output schema).
 * Out: NDJSON events on stdout, one JSON object per line, ending in `result`.
 *
 * Nothing is remembered between runs: the container starts, works and exits.
 */
import { spawn } from "node:child_process";
import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";

const OUT = process.stdout;

function emit(event) {
  OUT.write(JSON.stringify(event) + "\n");
}

function log(...args) {
  // stdout is the protocol; anything human goes to stderr.
  console.error("[agent-run]", ...args);
}

function readJob() {
  const raw = process.env.AGENT_JOB;
  if (!raw) throw new Error("AGENT_JOB is not set");
  return JSON.parse(Buffer.from(raw, "base64").toString("utf8"));
}

function renderPrompt(job) {
  const parts = [];
  if (job.history?.length) {
    parts.push("Conversation so far:");
    for (const message of job.history) {
      const who = message.role === "assistant" ? "Assistant" : "User";
      parts.push(`${who}: ${message.content}`);
    }
    parts.push("---");
  }
  parts.push(job.prompt ?? "");
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
  const fenced = text.match(/```(?:json)?\s*([\s\S]*?)```/);
  const candidates = [fenced?.[1], text];
  for (const candidate of candidates) {
    if (!candidate) continue;
    const trimmed = candidate.trim();
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start === -1 || end <= start) continue;
    try {
      return JSON.parse(trimmed.slice(start, end + 1));
    } catch {
      /* try the next candidate */
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
  // Whatever the agent set ships (AGENTS.md and friends) becomes the context for
  // this call. The container is new every time, so this is the only way in.
  if (existsSync("/workspace-defaults")) {
    cpSync("/workspace-defaults", workspace, { recursive: true });
  }
  writeFileSync(`${workspace}/JOB.json`, JSON.stringify({ ...job, history: undefined }, null, 2));

  const prompt = renderPrompt(job);
  const args = ["--mode", "json", "--no-session", "--provider", "nautionette",
                "--model", model, "--approve"];
  if (job.system_prompt) args.push("--append-system-prompt", job.system_prompt);
  args.push("--", prompt);

  const child = spawn("pi", args, {
    cwd: workspace,
    env: {
      ...process.env,
      AGENT_MODEL: model,
      NAUTIONETTE_MODE: mode,
      // Empty means "every federated tool"; a list narrows the bridge.
      NAUTIONETTE_TOOLS: Array.isArray(job.tools) ? job.tools.join(",") : "",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let finalText = "";
  let streamed = "";
  let stderr = "";
  let buffer = "";
  let runError = "";

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
    switch (event.type) {
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
        break;
      default:
        break;
    }
  }

  const code = await new Promise((resolve) => {
    child.on("error", (error) => {
      log("failed to start pi:", error.message);
      stderr += `\n${error.message}`;
      resolve(127);
    });
    child.on("close", resolve);
  });

  const text = (finalText || streamed).trim();

  if (!text && (runError || code !== 0)) {
    emit({
      type: "result",
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
      emit({
        type: "result",
        ok: false,
        text,
        output: null,
        error: "the model returned text where a structured object was declared",
      });
      return;
    }
    const problems = checkSchema(parsed, job.output_schema);
    if (problems.length) {
      emit({ type: "result", ok: false, text, output: parsed, error: problems.join("; ") });
      return;
    }
    emit({ type: "result", ok: true, text, output: parsed });
    return;
  }

  emit({ type: "result", ok: true, text, output: null });
}

main().catch((error) => {
  emit({ type: "result", ok: false, text: "", output: null, error: String(error?.message ?? error) });
  process.exitCode = 1;
});
