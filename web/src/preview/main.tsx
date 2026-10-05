import { createRoot } from 'react-dom/client';
import { App } from '../App';
import { ApiError } from '../api/client';
import { createEnvironment } from '../runtime/environment';
import { PreviewApi } from './api';
import type { PreviewMessage } from './contracts';
import '../styles.css';
import '../app.css';

const root = createRoot(document.getElementById('root')!);
const environment = { ...createEnvironment(false), openLink: () => {},
  importSubscription: async () => false, payment: async () => { throw new ApiError('preview_only'); } };
// Only the embedding window supplies configuration. Never accept credentials or a transport.
window.addEventListener('message', event => {
  if (window.parent === window || event.source !== window.parent || event.origin !== new URL(document.baseURI).origin) return;
  const message = event.data as PreviewMessage;
  if (message?.type !== 'yadreno.preview' || message.context?.contract_version !== 1 || !message.installation?.settings) return;
  history.replaceState(null, '', '#' + message.context.route);
  root.render(<App key={JSON.stringify(message.context)} environment={environment} transport={new PreviewApi(message.installation, message.context)} preview />);
});
window.parent.postMessage({ type: 'yadreno.preview.ready' }, '*');
window.addEventListener('hashchange', () => window.parent.postMessage({ type: 'yadreno.preview.route', route: location.hash.slice(1) }, '*'));
window.addEventListener('pointerdown', () => window.parent.postMessage({ type: 'yadreno.preview.interaction' }, '*'));
