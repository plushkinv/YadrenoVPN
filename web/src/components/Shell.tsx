import type { ReactNode } from 'react';
import { Home, Layers2, Ellipsis, LifeBuoy, Moon, Sun, UserRound, Zap } from 'lucide-react';
import { Button } from './Ui';
import { ru } from '../i18n/ru';
import type { Preset, Route, Theme } from '../model';
import { replaceable } from '../runtime/overrides';

function BaseShell({ children, route, navigate, preset, theme, toggleTheme, help, accountLabel, review, title = ru.brand, logo, account, navigation: customNavigation }: {
  children: ReactNode; route: Route; navigate: (route: Route) => void; preset: Preset; theme: Theme;
  toggleTheme: () => void; help: () => void; accountLabel: string; review: ReactNode;
  title?: string; logo?: string | null; account?: () => void;
  navigation?: { route: Route; label: string }[];
}) {
  const active = route === 'connect' ? 'home' : ['purchase', 'payment', 'renewal', 'subscription', 'devices', 'hosts', 'history', 'operation'].includes(route) ? 'subscriptions' : ['account', 'help', 'payments', 'balance', 'promotion', 'referrals', 'import', 'credentials', 'telegram-link'].includes(route) ? 'more' : route;
  const navigation = (customNavigation ?? [{ route: 'home', label: ru.home }, { route: 'subscriptions', label: ru.subscriptions }, { route: 'more', label: ru.more }]).map(item => ({ ...item, icon: item.route === 'home' ? Home : item.route === 'more' ? Ellipsis : Layers2 }));
  const brand = <>{logo ? <img className="brand-logo" src={logo} alt="" /> : <span className="brand-symbol"><Zap size={23} strokeWidth={2.6} /></span>}<span>{title}{title === ru.brand && <small>{ru.brandCaption}</small>}</span></>;
  const links = navigation.map(item => <a key={item.route} href={`#${item.route}`} onClick={event => { event.preventDefault(); navigate(item.route); }}
    className={`nav-link ${active === item.route ? 'is-active' : ''}`} aria-current={active === item.route ? 'page' : undefined}>
    <item.icon size={22} /><span>{item.label}</span><span className="nav-active-dot" /></a>);
  return <div className="app" data-preset={preset} data-theme={theme} data-ui="app.shell">
    <a className="skip-link" href="#main-content">{ru.skip}</a>
    {review}
    <div className="app-layout">
      <aside className="sidebar">
        <a className="brand" href="#home" aria-label={`${title} — ${ru.home}`}>{brand}</a>
        <div className="sidebar-label">{ru.cabinet}</div>
        <nav className="desktop-nav" aria-label={ru.navigation} data-ui="navigation.desktop">{links}</nav>
        <button type="button" className="sidebar-help" onClick={help}><LifeBuoy size={22} /><span><strong>{ru.help}</strong><small>{ru.helpCaption}</small></span></button>
        <div className="sidebar-foot">{ru.brand} {ru.brandCaption} <span>•</span> {ru.brandFooter}</div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <a className="brand mobile-brand" href="#home" aria-label={title} title={title}>{brand}</a>
          <span className="topbar-label"><span className="live-dot" />{ru.cabinet}</span>
          <div className="topbar-actions">
            <Button tone="quiet" className="icon-button" onClick={toggleTheme} aria-label={theme === 'light' ? ru.darkToggle : ru.lightToggle}>
              {theme === 'light' ? <Moon size={20} /> : <Sun size={20} />}
            </Button>
            <button type="button" className="account-chip" onClick={account} aria-label={accountLabel}><UserRound size={17} /><span>{accountLabel}</span></button>
          </div>
        </header>
        <main id="main-content" className="main-content" tabIndex={-1} data-ui={`page.${route}`}>{children}</main>
        <footer className="workspace-footer"><span>{ru.footer}</span><button type="button" onClick={help}>{ru.help}<LifeBuoy size={15} /></button></footer>
      </div>
    </div>
    <nav className="mobile-nav" aria-label={ru.mobileNavigation} data-ui="navigation.mobile">{links}</nav>
  </div>;
}

export const Shell = replaceable('app.shell', BaseShell);
