import { useEffect, useRef, useState } from 'react';
import { Button } from '../components/Ui';
import { Field } from '../components/Forms';
import { useApp } from '../runtime/context';
import { registeredPages as basePages, customization } from '../runtime/registry';
import { appText as t } from '../i18n/app';
import { scenarios, type InstallationPreview, type PreviewContext } from './contracts';
import declarations from '../runtime/view-registry.json';
import { AdminEditor, type CandidatePreview } from './AdminEditor';

function previewRoute(page: string) {
  const parameter = declarations.pages.find(item => item.id === page)?.preview_parameter;
  return page + (parameter ? '/' + parameter : '');
}

export function AdminPreview() {
  const { api, session, environment, settings } = useApp();
  const [configuration, setConfiguration] = useState<InstallationPreview>();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [controls, setControls] = useState(true);
  const [editing, setEditing] = useState(false);
  const [candidate, setCandidate] = useState<CandidatePreview>();
  const [viewNotice, setViewNotice] = useState('');
  const [context, setContext] = useState<PreviewContext>({ contract_version: 1, route: 'home', scenario: 'active',
    preset: settings.preset, theme: settings.theme, ui_version: customization.build_version, customization_version: customization.version });
  const viewed = useRef(context);
  const [visibleContext, setVisibleContext] = useState(context);
  const frame = useRef<HTMLIFrameElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const verified = environment.kind === 'telegram' && session?.source === 'mini_app';
  useEffect(() => { setCandidate(undefined); setEditing(false); setOpen(false); }, [session?.account_id]);
  useEffect(() => {
    let active = true;
    const check = async () => {
      if (!verified) { setConfiguration(undefined); setCandidate(undefined); setEditing(false); setOpen(false); return; }
      try { const value = await api.request<InstallationPreview>('/admin/ui/preview'); if (active) setConfiguration(value); }
      catch { if (active) { setConfiguration(undefined); setCandidate(undefined); setEditing(false); setOpen(false); } }
    };
    void check();
    const visible = () => { if (document.visibilityState === 'visible') void check(); };
    const timer = window.setInterval(visible, settings.sync_interval_seconds * 1000);
    document.addEventListener('visibilitychange', visible);
    return () => { active = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [api, session?.account_id, verified, settings.sync_interval_seconds]);
  useEffect(() => {
    if (!open || !configuration) return;
    dialog.current?.showModal();
    const send = () => frame.current?.contentWindow?.postMessage({ type: 'yadreno.preview', installation: configuration, context: viewed.current }, '*');
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== 'null') return;
      if (event.data?.type === 'yadreno.preview.ready') send();
      if (event.data?.type === 'yadreno.preview.route' && typeof event.data.route === 'string'
        && /^[a-z][a-z0-9_.-]*(?:\/[a-zA-Z0-9_.:-]+)?$/.test(event.data.route)) {
        // Readable integration context, never a privileged command channel.
        viewed.current = { ...viewed.current, route: event.data.route }; setVisibleContext(viewed.current);
        window.dispatchEvent(new CustomEvent('yadreno:preview-context', { detail: viewed.current }));
      }
      if (event.data?.type === 'yadreno.preview.appearance' && ['clear', 'signal', 'friendly'].includes(event.data.preset) && ['light', 'dark'].includes(event.data.theme)) {
        viewed.current = { ...viewed.current, preset: event.data.preset, theme: event.data.theme }; setVisibleContext(viewed.current);
        window.dispatchEvent(new CustomEvent('yadreno:preview-context', { detail: viewed.current }));
      }
    };
    send(); window.addEventListener('message', receive);
    window.dispatchEvent(new CustomEvent('yadreno:preview-context', { detail: viewed.current }));
    return () => { window.removeEventListener('message', receive); window.dispatchEvent(new CustomEvent('yadreno:preview-context', { detail: null })); };
  }, [open, context, configuration, candidate?.preview_url]);
  function showCandidate(value: CandidatePreview) {
    const exists = value.pages.some(page => page.id === viewed.current.route.split('/')[0]);
    const route = exists ? viewed.current.route : previewRoute(value.pages.find(page => page.id === 'home')?.id ?? value.pages[0]?.id ?? 'home');
    viewed.current = { ...viewed.current, route, ui_version: value.candidate.build_id,
      customization_version: value.candidate.customization_version };
    setContext(viewed.current); setVisibleContext(viewed.current); setCandidate(value);
    setViewNotice(exists ? '' : 'Выбранной страницы больше нет в этой версии. Открыта доступная страница.');
  }
  async function select(update: Partial<PreviewContext>) {
    setPending(true);
    try { const value = await api.request<InstallationPreview>('/admin/ui/preview'); setConfiguration(value); viewed.current = { ...viewed.current, ...update }; setContext(viewed.current); setVisibleContext(viewed.current); setOpen(true); }
    catch { setOpen(false); setConfiguration(undefined); }
    finally { setPending(false); }
  }
  if (!verified || !configuration) return null;
  return <><div className="admin-preview-launch" data-ui="admin.preview"><span>{t.adminPreview}</span><Button tone="secondary" disabled={pending} onClick={() => select({})}>{t.preview}</Button></div>
    {open && <dialog ref={dialog} className="admin-preview-dialog" onCancel={event => { event.preventDefault(); setOpen(false); }} aria-label={t.adminPreview}>
      <div className="admin-preview-controls">
        <div className="button-row"><strong>{t.previewData}</strong><Button tone="quiet" onClick={() => setControls(value => !value)}>{controls ? 'Смотреть интерфейс' : t.preview}</Button><Button tone="secondary" onClick={() => setEditing(value => !value)}>{editing ? 'Скрыть редактор' : 'Редактировать'}</Button><Button tone="quiet" onClick={() => setOpen(false)}>{t.previewClose}</Button></div>
        {controls && <fieldset disabled={pending} className="admin-preview-fields">
          <Field label={t.page}><select value={visibleContext.route.split('/')[0]} onChange={e => select({ route: previewRoute(e.target.value) })}>{(candidate?.pages ?? basePages).map(page => <option value={page.id} key={page.id}>{page.title ?? basePages.find(base => base.id === page.id)?.title ?? page.id}</option>)}</select></Field>
          <Field label={t.scenario}><select value={visibleContext.scenario} onChange={e => select({ scenario: e.target.value as PreviewContext['scenario'] })}>{Object.entries(scenarios).map(([id, title]) => <option value={id} key={id}>{title}</option>)}</select></Field>
          <Field label={t.appearance}><select value={visibleContext.preset} onChange={e => select({ preset: e.target.value as PreviewContext['preset'] })}><option value="clear">Universal / Clear</option><option value="signal">Signal / Dark</option><option value="friendly">Friendly / Brand</option></select></Field>
          <Field label="Тема"><select value={visibleContext.theme} onChange={e => select({ theme: e.target.value as PreviewContext['theme'] })}><option value="light">{t.light}</option><option value="dark">{t.dark}</option></select></Field>
        </fieldset>}
        <small>Тарифы и настройки — из вашей установки. Подписки и заказы — сценарий просмотра. Цены без персональных льгот.</small>
        {candidate && <p className="admin-editor-version">{candidate.published ? 'Показана применённая версия.' : 'Показан черновик. Для клиентов версия не изменена.'}</p>}
        {viewNotice && <p role="status">{viewNotice}</p>}
        {editing && <AdminEditor viewed={visibleContext} onCandidate={showCandidate} />}
      </div>
      <iframe ref={frame} src={candidate?.preview_url ?? customization.asset_base + 'preview.html'} sandbox="allow-scripts" title={t.preview} referrerPolicy="no-referrer" />
    </dialog>}
  </>;
}
