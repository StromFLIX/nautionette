// Loaded for chats regardless of agent set. Abort inside Pi's boundary hook:
// an RPC supervisor alone can race the next model request after compaction_end.
export default function (pi) {
  let afterAnswer = false;
  pi.on('agent_start', () => { afterAnswer = false; });
  pi.on('message_end', event => {
    if (event.message.role === 'assistant') afterAnswer = event.message.stopReason === 'stop';
  });
  pi.on('session_compact_failed', (event, ctx) => {
    // Native retries are exhausted. Do not retry the same broken checkpoint
    // after each tool batch. Leave optional post-answer maintenance non-fatal.
    const optional = event.reason === 'threshold' && afterAnswer;
    if (event.errorMessage && !event.aborted && !optional) ctx.abort();
  });
}
