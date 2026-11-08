// Raw mono PCM leaves the audio rendering thread in bounded blocks.
class DictationProcessor extends AudioWorkletProcessor {
  constructor () {
    super(); this.buffer = new Float32Array(2048); this.offset = 0
    this.port.onmessage = event => {
      if (event.data === 'flush') {
        if (this.offset) this.port.postMessage(this.buffer.slice(0, this.offset))
        this.offset = 0
        this.port.postMessage('flushed')
      }
    }
  }
  process (inputs) {
    const channel = inputs[0]?.[0]
    if (channel) {
      let position = 0
      while (position < channel.length) {
        const count = Math.min(channel.length - position, this.buffer.length - this.offset)
        this.buffer.set(channel.subarray(position, position + count), this.offset)
        this.offset += count; position += count
        if (this.offset === this.buffer.length) {
          this.port.postMessage(this.buffer, [this.buffer.buffer])
          this.buffer = new Float32Array(2048); this.offset = 0
        }
      }
    }
    return true
  }
}
registerProcessor('dictation-pcm', DictationProcessor)
