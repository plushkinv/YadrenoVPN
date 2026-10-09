import type { ApplicationDescriptor } from './config';
import type { ApplicationRuntime } from './runtime/contract';

export async function loadApplication(element: HTMLElement, descriptor: ApplicationDescriptor, runtime: ApplicationRuntime, signal?: AbortSignal) {
  const links = descriptor.styles.map(url => {
    const element = document.createElement('link'); element.rel = 'stylesheet'; element.crossOrigin = 'anonymous'; element.href = url;
    return element;
  });
  try {
    await Promise.all(links.map(link => new Promise<void>((resolve, reject) => {
      const finish = (error?: Error) => {
        clearTimeout(timeout); signal?.removeEventListener('abort', abort);
        link.onload = link.onerror = null;
        if (error) reject(error); else resolve();
      };
      const abort = () => finish(new Error('Application loading cancelled'));
      const timeout = setTimeout(() => finish(new Error('Application style timed out')), 15000);
      if (signal?.aborted) { abort(); return; }
      signal?.addEventListener('abort', abort, { once: true });
      link.onload = () => finish();
      link.onerror = () => finish(new Error('Application style unavailable'));
      document.head.append(link);
    })));
    const module = await import(/* @vite-ignore */ descriptor.entry);
    signal?.throwIfAborted();
    if (typeof module.mount !== 'function') throw new Error('Application entry is invalid');
    const unmount = module.mount(element, runtime);
    return () => { unmount(); links.forEach(link => link.remove()); };
  } catch (error) { links.forEach(link => link.remove()); throw error; }
}
