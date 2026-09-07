export default function (pi) {
  pi.registerProvider('nautionette', {
    baseUrl: 'http://127.0.0.1:18765/v1',
    apiKey: 'test',
    api: 'openai-completions',
    models: [{
      id: 'test-model', name: 'Test model', reasoning: false, input: ['text'],
      cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32000, maxTokens: 2048,
    }],
  })
}