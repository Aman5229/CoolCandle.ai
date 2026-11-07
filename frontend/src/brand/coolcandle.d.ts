// public/coolcandle.js, loaded by index.html before the app starts.
interface CoolCandleLoaderApi {
  render(root?: ParentNode): void
  track<T>(promise: Promise<T>, options?: { text?: string; delay?: number }): Promise<T>
}

interface Window {
  CoolCandleLoader?: CoolCandleLoaderApi
}
