import { useApp, AppContext, useAction } from './runtime/context';
import { Shell } from './components/Shell';
import { Button } from './components/Ui';
import { registeredPages, customization } from './runtime/registry';
import { appText as t } from './i18n/app';
import { NativeConnection } from './components/NativeConnection';
import { Failure } from './components/Forms';

export function App() {
  const value = useApp();
  const { session, settings, bootstrap, environment, preset, theme, route, param, navigate, status } = value;
  const action = useAction();
  const definition = registeredPages.find(page => page.id === route);
  const selected = definition && (definition.public || session) ? definition : registeredPages.find(page => page.id === 'login')!;
  const Component = selected.component;
  return <AppContext.Provider value={{ ...value, route: selected.id }}><Shell route={selected.id} activeRoute={value.section}
    navigate={navigate} preset={preset} theme={theme} title={settings.title}
    logo={settings.logo?.startsWith('/ui/assets/') ? customization.asset_base + settings.logo.slice(4) : settings.logo}
    accountLabel={session ? `${t.account} ${session.account_id}` : t.login} account={() => navigate('account')}
    help={() => navigate('help')} toggleTheme={() => value.setTheme(theme === 'light' ? 'dark' : 'light')}>
    {(status.offline || status.cachedAt) && <p className="notice" role="status">{t.offline}{status.cachedAt && ` ${t.updated}: ${new Date(status.cachedAt).toLocaleString('ru')}`}</p>}
    {status.newUi && <p className="notice" role="status">Доступна новая версия интерфейса. <Button tone="quiet" disabled={status.updating}
      onClick={() => void action.run(value.update)}>Обновить интерфейс</Button></p>}
    <Failure error={action.error} />
    {environment.native && !['home', 'connect'].includes(selected.id) && <NativeConnection bridge={environment.native} />}
    {!definition ? <h1>{t.notFound}</h1> : definition.feature && !bootstrap.features[definition.feature] ? <p>{t.empty}</p>
      : definition.module_id && !bootstrap.modules?.some(module => module.module_id === definition.module_id && module.state === 'available' && module.api_version === 1)
      ? <p role="alert">Модуль этой страницы недоступен. Повторите позже.</p> : <Component key={`${selected.id}/${param}/${session?.account_id}`} />}
    {!session && selected.id !== 'login' && <Button onClick={() => navigate('login')}>{t.login}</Button>}
  </Shell></AppContext.Provider>;
}
