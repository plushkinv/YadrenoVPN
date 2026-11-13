// The capability stays in the fragment and request body, never in access-log URLs.
const parameters = new URLSearchParams(location.hash.slice(1));
const token = parameters.get('token');
const client = parameters.get('client');
const status = document.getElementById('status');
const actions = document.getElementById('actions');
const open = document.getElementById('open');
const subscription = document.getElementById('subscription');
const retry = document.getElementById('retry');
const messages = JSON.parse(document.getElementById('messages').textContent);
const text = (key, client) => messages[key].replace('{client}', client ?? '');

async function prepare() {
  retry.hidden = true;
  if (!token || !client) {
    status.textContent = text('incomplete');
    return;
  }
  status.textContent = text('preparing');
  try {
    // The external browser need not share the Mini App's session. Authorization
    // is the signed capability; ignore unrelated cabinet cookies explicitly.
    const response = await fetch('/api/v1/client-import/resolve', {
      method: 'POST', credentials: 'omit', cache: 'no-store',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ token, client }),
    });
    if (!response.ok) {
      status.textContent = response.status >= 500
        ? text('failed')
        : text('invalid');
      retry.hidden = response.status < 500;
      return;
    }
    const data = await response.json();
    open.href = data.uri;
    open.textContent = text('open_client', data.client_name);
    subscription.value = data.url;
    status.textContent = text('adding', data.client_name);
    actions.hidden = false;
    // Automatic navigation is best-effort. The ordinary link remains available
    // for a fresh trusted click if the browser requires user activation.
    try { location.href = data.uri; } catch {}
  } catch {
    status.textContent = text('offline');
    retry.hidden = false;
  }
}
document.getElementById('copy').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(subscription.value);
    document.getElementById('copy-status').textContent = text('copied');
  } catch {
    subscription.focus(); subscription.select();
    document.getElementById('copy-status').textContent = text('copy_manually');
  }
});
retry.addEventListener('click', prepare);
void prepare();
