// Recovery stays inside the live Pi session: never replay a cold job or a tool.
// Pi gets its own retry/compaction budget first. This budget belongs to the whole
// container call (including steered inputs), not to each successful model step.
import { randomUUID } from "node:crypto";

export const RECOVERY_DELAYS_MS = Object.freeze([2000, 5000]);

export function recoveryReason(error) {
  const text = String(error || "");
  // Permanent failures outrank transient-looking words/statuses in their details.
  if (/\b(?:400|401|402|403|404|405|413|422|501)\b|auth(?:entication|orization)?|unauthori[sz]ed|forbidden|permission|access.denied|api.key|credentials?|billing|credits?|quota|payment|spend.limit|budget|context.{0,30}(?:length|window|limit|overflow)|too.many.tokens|prompt.{0,20}too.long|invalid.(?:request|argument|parameter|model)|unsupported|not.found|content.filter|safety|policy|cancelled|canceled|aborted/i.test(text)) return null;
  if (/\b429\b|rate.limit|too.many.requests/i.test(text)) return "model rate limit";
  if (/\b(?:500|502|503|504|52[0-9])\b|overload|temporar(?:y|ily)|server.error|internal.error|service.unavailable/i.test(text)) return "temporary model service failure";
  if (/ECONNRESET|ECONNREFUSED|EPIPE|ETIMEDOUT|EAI_AGAIN|ENOTFOUND|UND_ERR_|fetch failed|network.error|connection.{0,30}(?:closed|reset|lost|failed|terminated)|stream.{0,30}(?:closed|disconnected|interrupted|terminated)|socket.hang.up|timed?.out|timeout|unexpected.(?:end|eof)|^terminated$/i.test(text)) return "interrupted model connection";
  if (/^(?:error:\s*)?(?:an?\s+)?unknown (?:model )?(?:error|failure)(?: occurred)?[.!]?$/i.test(text.trim())) return "unknown model error";
  return null;
}

function continuationPrompt(reason) {
  // Never interpolate the provider's raw error: it is untrusted diagnostic data
  // and can contain secrets or instructions. The reason is a local fixed label.
  return `Nautionette automatic recovery notice (not a new user request): ${reason}.
The previous model request failed. This is the SAME live session and workspace; completed tool calls and their results are still in context.
Review the user's latest instructions, work already completed, and tool results. Then continue only the unfinished work from the point of failure; do not restart the task or repeat completed actions.
Preserve existing edits and commits. Before retrying any failed or uncertain external action, inspect its current state with a read-only check. If you cannot establish whether it already succeeded, stop and report that concrete blocker instead of risking a duplicate action.
Do not change permissions, credentials, model, configuration, or the scope of the user's request to get past an error. Follow all existing instructions and stop requests. Briefly report the recovery and continue; if the task is already complete, give the final answer.`;
}

export function createChatRecovery({ control, emit, finish, schedule = setTimeout, cancel = clearTimeout }) {
  let modelError = "";
  let compactionError = "";
  let fatalError = "";
  let promptId = null;
  let lastStopReason = null;
  let continuationPending = false;
  let cancelled = false;
  let settled = false;
  let attempts = 0;
  let timer = null;
  let closed = false;
  const tools = new Set();

  function observe(event) {
    if (event.type === "agent_start") lastStopReason = null;
    if (event.type === "agent_start" && continuationPending) {
      continuationPending = false;
      if (timer !== null) {
        // Late native/extension work won the race. Do not inject another prompt.
        cancel(timer);
        timer = null;
        control.cancelRecovery();
      }
    }
    if (event.type === "response" && promptId && event.id === promptId) {
      promptId = null;
      if (!event.success || event.data?.disposition === "handled") {
        fatalError = event.error || "The automatic continuation was not accepted by Pi.";
        finish();
      }
    }
    if (event.type === "tool_execution_start") tools.add(event.toolCallId);
    if (event.type === "tool_execution_end") tools.delete(event.toolCallId);
    if (event.type === "message_end" && event.message?.role === "assistant") {
      const message = event.message;
      lastStopReason = message.stopReason || "stop";
      if (message.stopReason === "error") modelError = message.errorMessage || "Unknown model error";
      else if (message.stopReason === "aborted") {
        cancelled = true;
        modelError = message.errorMessage || "The model response was aborted.";
      } else {
        // Also clears errors recovered by compaction/steering, not just auto_retry.
        modelError = compactionError = "";
      }
    }
    if (event.type === "auto_retry_end") {
      if (event.success && !cancelled) modelError = "";
      else if (event.finalError) {
        modelError = event.finalError;
        if (/cancelled|canceled|aborted/i.test(event.finalError)) cancelled = true;
      }
    }
    if (event.type === "compaction_end") {
      // Post-answer threshold maintenance does not invalidate a completed reply;
      // the container will be discarded anyway. Pre-answer/overflow failures do.
      const afterAnswer = event.reason === "threshold" && lastStopReason === "stop";
      if (event.aborted && !afterAnswer) cancelled = true;
      // Pi already owns compact-and-retry. Do not bypass its failure/cancellation.
      if (event.errorMessage && !afterAnswer) compactionError = event.errorMessage;
    }
    if (control && event.type === "auto_retry_start") emit({
      type: "status", state: "retrying",
      message: `Retrying the model request (${event.attempt}/${event.maxAttempts})`,
    });
    if (control && event.type === "compaction_start") emit({
      type: "status", state: "compacting", message: "Recovering space in the model context",
    });
  }

  function settle() {
    if (!control || closed || settled || continuationPending) return;
    if (tools.size) fatalError ||= "A tool did not report its result. Automatic continuation is unsafe; verify its outcome before retrying.";
    if (!modelError && !compactionError && !cancelled && lastStopReason !== "stop") {
      fatalError ||= "The model stopped before completing its response. Partial work was retained.";
    }
    const reason = !fatalError && !compactionError && !cancelled && recoveryReason(modelError);
    if (reason && attempts < RECOVERY_DELAYS_MS.length && control.beginRecovery()) {
      const delay = RECOVERY_DELAYS_MS[attempts++];
      continuationPending = true;
      emit({ type: "recovery", attempt: attempts, max_attempts: RECOVERY_DELAYS_MS.length,
        reason, message: `Automatic recovery ${attempts}/${RECOVERY_DELAYS_MS.length}: ${reason}` });
      emit({ type: "status", state: "recovering",
        message: `Recovering automatically in ${delay / 1000}s (${attempts}/${RECOVERY_DELAYS_MS.length}); keeping completed work` });
      timer = schedule(() => {
        timer = null;
        if (closed) return;
        promptId = `nautionette-recovery-${randomUUID()}`;
        if (cancelled || fatalError || !control.continueRecovery(promptId, continuationPrompt(reason))) {
          settled = true;
          finish();
          return;
        }
        emit({ type: "status", state: "recovering", message: "Reviewing completed work and continuing in the same session" });
      }, delay);
      return;
    }
    settled = true;
    finish();
  }

  return {
    observe, settle,
    fail(error) { fatalError = String(error); if (timer !== null) cancel(timer); timer = null; },
    close() { closed = true; if (timer !== null) cancel(timer); timer = null; },
    get error() { return fatalError || compactionError || modelError || (cancelled ? "The agent operation was aborted." : ""); },
    get settled() { return settled; },
    get attempts() { return attempts; },
  };
}
