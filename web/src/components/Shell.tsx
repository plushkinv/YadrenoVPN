import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { Home, Layers2, Ellipsis, Moon, Sun } from 'lucide-react';
import { Button } from './Ui';
import { ru } from '../i18n/ru';
import type { Preset, Route, Theme } from '../model';
import { navigationSection } from '../runtime/navigation';

function BaseShell({ children, route, activeRoute, navigate, preset, theme, toggleTheme, title = ru.cabinet, logo, navigation: customNavigation }: {
  children: ReactNode; route: Route; navigate: (route: Route) => void; preset: Preset; theme: Theme;
  toggleTheme: () => void; help: () => void; accountLabel: string;
  title?: string; logo?: string | null; account?: () => void;
  navigation?: { route: Route; label: string }[];
  activeRoute?: Route;
}) {
  const app = useRef<HTMLDivElement>(null), nav = useRef<HTMLElement>(null);
  const active = customNavigation?.some(item => item.route === route) ? route : activeRoute ?? navigationSection(route);
  const navigation = (customNavigation ?? [{ route: 'home', label: ru.home }, { route: 'subscriptions', label: ru.subscriptions }, { route: 'more', label: ru.more }]).map(item => ({ ...item, icon: item.route === 'home' ? Home : item.route === 'more' ? Ellipsis : Layers2 }));
  const [failedLogo, setFailedLogo] = useState<string>();
  useLayoutEffect(() => {
    const resize = () => {
      app.current?.style.setProperty('--navigation-height', (nav.current?.getBoundingClientRect().height ?? 0) + 'px');
    };
    const observer = new ResizeObserver(resize);
    if (nav.current) observer.observe(nav.current);
    resize();
    return () => observer.disconnect();
  }, []);
  const links = navigation.map(item => <a key={item.route} href={'#' + item.route} onClick={event => { event.preventDefault(); navigate(item.route); }}
    className={'nav-link' + (active === item.route ? ' is-active' : '')} aria-current={active === item.route ? 'page' : undefined}>
    <item.icon size={22} aria-hidden="true" /><span>{item.label}</span></a>);
  return <div ref={app} className="app" data-preset={preset} data-theme={theme} data-ui="app.shell">
    <a className="skip-link" href="#main-content" onClick={event => { event.preventDefault(); document.getElementById('main-content')?.focus(); }}>{ru.skip}</a>
    <header className="app-header"><div className="header-inner">
      <a className="brand" href="#home" onClick={event => { event.preventDefault(); navigate('home'); }} aria-label={title + ' — ' + ru.home} title={title}>
        {logo && logo !== failedLogo && <img className="brand-logo" src={logo} alt="" onError={() => setFailedLogo(logo)} />}<span>{title}</span>
      </a>
      <Button tone="quiet" className="icon-button" onClick={toggleTheme} aria-label={theme === 'light' ? ru.darkToggle : ru.lightToggle}>
        {theme === 'light' ? <Moon size={22} aria-hidden="true" /> : <Sun size={22} aria-hidden="true" />}
      </Button>
    </div></header>
    <div className="app-layout"><aside className="sidebar"><p className="section-kicker">{ru.cabinet}</p>
      <nav className="desktop-nav" aria-label={ru.navigation} data-ui="navigation.desktop">{links}</nav>
    </aside><main id="main-content" className="main-content" tabIndex={-1} data-ui={'page.' + route}>{children}</main></div>
    <nav ref={nav} className="mobile-nav" aria-label={ru.mobileNavigation} data-ui="navigation.mobile">{links}</nav>
  </div>;
}
export const Shell = BaseShell;
