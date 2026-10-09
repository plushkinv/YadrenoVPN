import { ApiError, type Api } from '../api/client';
import type { Session } from '../api/contracts';

type LoginResult = { id_token?: string; error?: string };
type LoginLibrary = { auth(options: { client_id: number; nonce: string; scope: string[]; lang: string }, callback: (result: LoginResult) => void): void };
type TelegramWindow = Window & { Telegram?: { Login?: LoginLibrary } };
let loading: Promise<LoginLibrary> | undefined;

function library(): Promise<LoginLibrary> {
  const existing = (window as TelegramWindow).Telegram?.Login;
  if (existing) return Promise.resolve(existing);
  if (!loading) loading = new Promise<LoginLibrary>((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://oauth.telegram.org/js/telegram-login.js?6'; script.async = true;
    const timer = setTimeout(() => failed(), 15000);
    const failed = () => { clearTimeout(timer); script.remove(); reject(new ApiError('telegram_login_unavailable', true)); };
    script.onerror = failed;
    script.onload = () => {
      clearTimeout(timer);
      const login = (window as TelegramWindow).Telegram?.Login;
      if (login) resolve(login); else failed();
    };
    document.head.append(script);
  }).catch(error => { loading = undefined; throw error; });
  return loading;
}

/** Keep JWTs inside the system runtime and preserve the original click's popup permission. */
export async function loginWithTelegram(api: Api): Promise<Session | null> {
  const popup = window.open('about:blank', 'telegram_oidc_login', 'popup,width=550,height=650');
  if (!popup) throw new ApiError('telegram_login_popup_blocked');
  try {
    const [login, challenge] = await Promise.all([library(), api.request<{ client_id: number; nonce: string; expires_at: number }>('/auth/telegram/web/start', 'POST', {})]);
    if (popup.closed) return null;
    const result = await new Promise<LoginResult>(resolve => {
      const timer = setTimeout(() => resolve({ error: 'expired' }), Math.max(0, challenge.expires_at * 1000 - Date.now()));
      login.auth({ client_id: challenge.client_id, nonce: challenge.nonce, scope: ['profile'], lang: 'ru' }, value => { clearTimeout(timer); resolve(value); });
    });
    if (result.error === 'popup_closed') return null;
    if (!result.id_token) throw new ApiError('telegram_authentication_failed');
    return await api.request<Session>('/auth/telegram/web/finish', 'POST', { id_token: result.id_token });
  } finally { popup.close(); }
}
