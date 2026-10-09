/** Used only for an entry without an in-app predecessor. */
export function parentRoute(route: string) {
  const [page, ...parts] = route.split('/');
  const param = parts.join('/');
  if (['connect', 'renewal', 'devices', 'hosts', 'history'].includes(page) && param) return 'subscription/' + param;
  if (['subscription', 'purchase', 'operation', 'trials'].includes(page)) return 'subscriptions';
  if (page === 'payment') return 'payments';
  if (['credentials', 'telegram-link'].includes(page)) return 'account';
  if (['account', 'payments', 'balance', 'promotion', 'referrals', 'import', 'help'].includes(page)) return 'more';
  if (['register', 'reset'].includes(page)) return 'login';
  return 'home';
}

export function navigationSection(route: string) {
  const page = route.split('/')[0];
  if (['connect', 'trials', 'purchase', 'renewal', 'subscription', 'devices', 'hosts', 'history', 'operation'].includes(page)) return 'subscriptions';
  if (['account', 'help', 'payment', 'payments', 'balance', 'promotion', 'referrals', 'import', 'credentials', 'telegram-link'].includes(page)) return 'more';
  return page;
}
