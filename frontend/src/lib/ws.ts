/** The one WebSocket. Reconnects by itself, and remembers what it was watching. */

export interface Tick {
  instrument_key: string
  ts: string
  ltp: string
  bid: string | null
  ask: string | null
}

export type Handler = (data: never) => void

class Live {
  private socket: WebSocket | null = null
  private handlers = new Map<string, Set<Handler>>()
  private keys = new Set<string>()
  private retry = 1000
  private closed = true

  start(): void {
    if (!this.closed) return
    this.closed = false
    this.open()
  }

  stop(): void {
    this.closed = true
    this.socket?.close()
    this.socket = null
  }

  private open(): void {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${scheme}://${location.host}/ws`)
    this.socket = socket
    socket.onopen = () => {
      this.retry = 1000
      if (this.keys.size) this.send()
    }
    socket.onmessage = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as { type: string; data: never }
      this.handlers.get(event.type)?.forEach((handler) => handler(event.data))
    }
    socket.onclose = () => {
      if (this.closed) return
      setTimeout(() => this.open(), this.retry)
      this.retry = Math.min(this.retry * 2, 15000)
    }
  }

  private send(): void {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: 'subscribe', keys: [...this.keys] }))
    }
  }

  watch(keys: string[]): void {
    const fresh = keys.filter((key) => !this.keys.has(key))
    if (fresh.length === 0) return
    fresh.forEach((key) => this.keys.add(key))
    this.send()
  }

  on<T>(type: string, handler: (data: T) => void): () => void {
    const set = this.handlers.get(type) ?? new Set<Handler>()
    set.add(handler as Handler)
    this.handlers.set(type, set)
    return () => set.delete(handler as Handler)
  }
}

export const live = new Live()
