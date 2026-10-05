import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Check, MessageSquareText, Settings, X } from 'lucide-react';
import { Field } from '../components/Forms';
import type { UiSettings } from '../api/contracts';
import { useApp } from '../runtime/context';
import { registeredPages as basePages, customization } from '../runtime/registry';
import { appText as t, errorText } from '../i18n/app';
import { scenarios, type InstallationPreview, type PreviewContext } from './contracts';
import declarations from '../runtime/view-registry.json';
import { AdminEditor, EditorIcon } from './AdminEditor';
import { useAdminEditor, type CandidatePreview } from './useAdminEditor';

function previewRoute(page: string) {
  const parameter = declarations.pages.find(item => item.id === page)?.preview_parameter;
  return page + (parameter ? '/' + parameter : '');
}

type Panel = 'cabinet' | 'preview' | 'editor' | 'settings';
type PublishedManifest = { manifest: { customization_version: string } };

export function AdminPreview({ onSettingsSaved }: { onSettingsSaved: (value: UiSettings) => void }) {
  const { api, session, environment, settings } = useApp();
  const [configuration, setConfiguration] = useState<InstallationPreview>();
  const recheck = useRef<() => void>(() => {});
  const verified = environment.kind === 'telegram' && session?.source === 'mini_app';
  useEffect(() => {
    let active = true;
    setConfiguration(undefined);
    const check = async () => {
      if (!verified) return;
      try { const value = await api.request<InstallationPreview>('/admin/ui/preview'); if (active) setConfiguration(value); }
      catch { if (active) setConfiguration(undefined); }
    };
    recheck.current = () => { void check(); };
    void check();
    const visible = () => { if (document.visibilityState === 'visible') void check(); };
    const timer = window.setInterval(visible, settings.sync_interval_seconds * 1000);
    document.addEventListener('visibilitychange', visible);
    return () => { active = false; recheck.current = () => {}; clearInterval(timer); document.removeEventListener('visibilitychange', visible); };
  }, [api, session?.account_id, verified, settings.sync_interval_seconds]);
  if (!verified || !configuration) return null;
  return <PreviewPanel key={session?.account_id} configuration={configuration} onSettingsSaved={onSettingsSaved} recheck={() => recheck.current()} />;
}

function PreviewPanel({ configuration, onSettingsSaved, recheck }: { configuration: InstallationPreview; onSettingsSaved: (value: UiSettings) => void; recheck: () => void }) {
  const { api, settings, route, theme } = useApp();
  const [panel, setPanel] = useState<Panel>('cabinet');
  const [pending, setPending] = useState(false), [error, setError] = useState('');
  const [customDesign, setCustomDesign] = useState<boolean>();
  const [candidate, setCandidate] = useState<CandidatePreview>();
  const [viewNotice, setViewNotice] = useState('');
  const [context, setContext] = useState<PreviewContext>({ contract_version: 1, route: previewRoute(route), scenario: 'active',
    preset: settings.preset, theme, ui_version: customization.build_version, customization_version: customization.version });
  const viewed = useRef(context);
  const [visibleContext, setVisibleContext] = useState(context);
  const frame = useRef<HTMLIFrameElement>(null), dialog = useRef<HTMLDialogElement>(null);
  const launch = useRef<HTMLDivElement>(null), fileInput = useRef<HTMLInputElement>(null);
  const closing = useRef(false);
  const previewStarted = useRef(false);
  const open = panel !== 'cabinet';
  function select(update: Partial<PreviewContext>) {
    viewed.current = { ...viewed.current, ...update }; setContext(viewed.current); setVisibleContext(viewed.current);
  }
  function selectDemo(update: Partial<PreviewContext>) { recheck(); select(update); }
  function showCandidate(value: CandidatePreview) {
    const exists = value.pages.some(page => page.id === viewed.current.route.split('/')[0]);
    const nextRoute = exists ? viewed.current.route : previewRoute(value.pages.find(page => page.id === 'home')?.id ?? value.pages[0]?.id ?? 'home');
    select({ route: nextRoute, ui_version: value.candidate.build_id, customization_version: value.candidate.customization_version });
    setCandidate(value);
    setViewNotice(exists ? '' : 'Выбранной страницы больше нет в этой версии. Открыта доступная страница.');
  }
  const editor = useAdminEditor({ viewed: visibleContext, onCandidate: showCandidate, polling: open, visible: panel === 'editor' });
  function activate(next: 'editor' | 'settings') {
    if (closing.current) return;
    recheck();
    previewStarted.current = true;
    if (!open) select({ route: previewRoute(route), preset: settings.preset, theme });
    flushSync(() => setPanel(next));
  }
  function close() {
    if (pending) return;
    closing.current = true;
    select({ preset: settings.preset }); setPanel('cabinet'); setError('');
    requestAnimationFrame(() => { launch.current?.querySelector<HTMLButtonElement>('[aria-label="Настройки просмотра"]')?.focus({ preventScroll: true }); closing.current = false; });
  }
  useLayoutEffect(() => {
    if (!open) { dialog.current?.close(); return; }
    dialog.current?.showModal();
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const viewport = () => {
      dialog.current?.style.setProperty('--admin-viewport-height', `${window.visualViewport?.height ?? window.innerHeight}px`);
      dialog.current?.style.setProperty('--admin-viewport-top', `${window.visualViewport?.offsetTop ?? 0}px`);
    };
    viewport();
    window.visualViewport?.addEventListener('resize', viewport); window.visualViewport?.addEventListener('scroll', viewport);
    return () => { document.body.style.overflow = previous; window.visualViewport?.removeEventListener('resize', viewport); window.visualViewport?.removeEventListener('scroll', viewport); };
  }, [open]);
  useEffect(() => {
    if (panel !== 'settings') return;
    let active = true;
    setPending(true); setError(''); setCustomDesign(undefined);
    api.request<PublishedManifest>('/ui/manifest').then(value => {
      if (active) setCustomDesign(value.manifest.customization_version !== 'base');
    }).catch(reason => { if (active) setError(errorText(reason)); }).finally(() => { if (active) setPending(false); });
    return () => { active = false; };
  }, [panel, api, candidate?.candidate.build_id, candidate?.published]);
  useEffect(() => {
    if (!open) return;
    const send = () => frame.current?.contentWindow?.postMessage({ type: 'yadreno.preview', installation: configuration, context: viewed.current }, '*');
    const receive = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow || event.origin !== 'null') return;
      if (event.data?.type === 'yadreno.preview.ready') send();
      if (event.data?.type === 'yadreno.preview.interaction') setPanel(current => current === 'editor' ? 'preview' : current);
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
  async function save() {
    if (pending) return;
    setPending(true); setError('');
    try {
      if (!customDesign && visibleContext.preset !== settings.preset) {
        const value = await api.request<UiSettings>('/admin/ui/preset', 'POST', { preset: visibleContext.preset });
        onSettingsSaved(value);
      }
      closing.current = true; setPanel('cabinet');
      requestAnimationFrame(() => { launch.current?.querySelector<HTMLButtonElement>('[aria-label="Настройки просмотра"]')?.focus({ preventScroll: true }); closing.current = false; });
    } catch (reason) { setError(errorText(reason)); }
    finally { setPending(false); }
  }
  const gear = <EditorIcon label="Настройки просмотра" onClick={() => activate('settings')}><Settings /></EditorIcon>;
  const composer = (expanded: boolean) => <AdminEditor editor={editor} expanded={expanded} onActivate={() => activate('editor')}
    onAttach={() => { fileInput.current?.click(); activate('editor'); }} tools={<>{gear}{open && <EditorIcon label="Выйти из просмотра" onClick={close}><X /></EditorIcon>}</>} />;
  return <>
    <input ref={fileInput} type="file" multiple hidden aria-label="Файлы для кастомизатора"
      onChange={event => { editor.selectFiles(event.target.files); event.target.value = ''; }} />
    <div ref={launch} className="admin-preview-launch" data-ui="admin.preview">{!open && composer(false)}</div>
    <dialog ref={dialog} className="admin-preview-dialog" onCancel={event => { event.preventDefault(); close(); }} aria-label="Просмотр интерфейса">
      {previewStarted.current &&
        <div className="admin-preview-page" inert={panel === 'settings'}>
          <iframe ref={frame} src={candidate?.preview_url ?? customization.asset_base + 'preview.html'} sandbox="allow-scripts" title={t.preview} referrerPolicy="no-referrer" />
          <span className="admin-preview-badge">{candidate && !candidate.published ? 'Демо · Черновик' : 'Демо'}</span>
        </div>}
      {open && <>
        {panel === 'settings' ? <div className="admin-settings-backdrop">
          <section className="admin-settings" aria-label="Настройки просмотра">
            <header className="admin-settings-header"><strong>Настройки</strong>
              <EditorIcon label="Вернуться к редактору" disabled={pending} onClick={() => activate('editor')}><MessageSquareText /></EditorIcon>
              <EditorIcon label="Отменить и закрыть" className="admin-icon--cancel" disabled={pending} onClick={close}><X /></EditorIcon>
              <EditorIcon label="Сохранить дизайн" className="admin-icon--save" disabled={pending || customDesign === undefined} onClick={() => void save()}><Check /></EditorIcon>
            </header>
            <fieldset disabled={pending} className="admin-preview-fields">
              <Field label={t.page}><select value={visibleContext.route.split('/')[0]} onChange={e => selectDemo({ route: previewRoute(e.target.value) })}>{(candidate?.pages ?? basePages).map(page => <option value={page.id} key={page.id}>{page.title ?? basePages.find(base => base.id === page.id)?.title ?? page.id}</option>)}</select></Field>
              <Field label={t.scenario}><select value={visibleContext.scenario} onChange={e => selectDemo({ scenario: e.target.value as PreviewContext['scenario'] })}>{Object.entries(scenarios).map(([id, title]) => <option value={id} key={id}>{title}</option>)}</select></Field>
              {customDesign ? <p className="admin-custom-design">Свой дизайн</p> : customDesign === false && <Field label={t.appearance}><select value={visibleContext.preset} onChange={e => { setError(''); selectDemo({ preset: e.target.value as PreviewContext['preset'] }); }}><option value="clear">Universal / Clear</option><option value="signal">Signal / Dark</option><option value="friendly">Friendly / Brand</option></select></Field>}
              <Field label="Тема просмотра"><select value={visibleContext.theme} onChange={e => selectDemo({ theme: e.target.value as PreviewContext['theme'] })}><option value="light">{t.light}</option><option value="dark">{t.dark}</option></select></Field>
            </fieldset>
            {error && <p role="alert">{error}</p>}
          </section>
        </div> : <div className="admin-preview-overlay">{composer(panel === 'editor')}{viewNotice && panel === 'editor' && <p role="status">{viewNotice}</p>}</div>}
      </>}
    </dialog>
  </>;
}
