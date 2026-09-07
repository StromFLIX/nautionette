import { createServer } from 'node:http'
import { spawn } from 'node:child_process'

let requests = 0
const server = createServer(async (request, response) => {
  let body = ''
  for await (const chunk of request) body += chunk
  const input = JSON.parse(body)
  response.writeHead(200, { 'Content-Type': 'text/event-stream' })
  const chunk = (delta, reason = null) => response.write(`data: ${JSON.stringify({
    id: 'chat-test', object: 'chat.completion.chunk', created: 1, model: 'test-model',
    choices: [{ index: 0, delta, finish_reason: reason }],
  })}\n\n`)
  chunk({ role: 'assistant', content: '' })
  if (requests++ === 0) {
    chunk({ tool_calls: [{ index: 0, id: 'blocked-command', type: 'function', function: {
      name: 'bash', arguments: JSON.stringify({ command: `node -e "const fs=require('fs');fs.writeFileSync('/tmp/tool-started','yes');const timer=setInterval(()=>{if(fs.existsSync('/tmp/release-tool')){clearInterval(timer);console.log('tool finished')}},20)"` }),
    } }] })
    chunk({}, 'tool_calls')
  } else {
    const messages = input.messages.filter((message) => message.role === 'user')
    const text = JSON.stringify(messages).includes('queued instruction') ? 'Received queued instruction after tool' : 'Missing queued instruction'
    chunk({ content: text })
    chunk({}, 'stop')
  }
  response.end('data: [DONE]\n\n')
})
server.listen(18765, '127.0.0.1', () => {
  const child = spawn('node', ['/workspace/agent-run.mjs'], { stdio: 'inherit' })
  child.on('exit', (code) => server.close(() => { process.exitCode = code || 0 }))
})