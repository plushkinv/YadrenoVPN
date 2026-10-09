import { createRoot } from 'react-dom/client';
import { useSyncExternalStore } from 'react';
import { App } from '@application/App';
import { AppContext, BackContext } from './runtime/context';
import { applicationContext } from './runtime/application-context';
import { bindApplicationStorage } from './runtime/application-storage';
import type { ApplicationRuntime } from './runtime/contract';
import { ApplicationBoundary } from './ApplicationBoundary';
import '@application/styles.css';
import '@application/app.css';

function Application({ runtime }: { runtime: ApplicationRuntime }) {
  const state = useSyncExternalStore(runtime.subscribe, runtime.getSnapshot);
  const context = applicationContext(runtime, state);
  bindApplicationStorage(context.storage);
  document.documentElement.style.colorScheme = state.theme;
  for (const [key, value] of Object.entries(state.insets)) document.documentElement.style.setProperty(key, value);
  return <BackContext.Provider value={context.back}><AppContext.Provider value={context}>
    <ApplicationBoundary reload={context.reload}><App /></ApplicationBoundary>
  </AppContext.Provider></BackContext.Provider>;
}

export function mount(element: HTMLElement, runtime: ApplicationRuntime): () => void {
  const root = createRoot(element);
  root.render(<Application runtime={runtime} />);
  return () => root.unmount();
}
