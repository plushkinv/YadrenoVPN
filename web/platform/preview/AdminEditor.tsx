import { useEffect, useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { ArrowUp, Check, ChevronDown, ChevronUp, Expand, LogOut, Mic, MoreHorizontal, Paperclip, Plus, RefreshCw, Square, Trash2, X } from 'lucide-react';
import { Button } from '../components/Ui';
import type { AdminEditorController } from './useAdminEditor';
import { VoiceMessage, VoiceWave, voiceTime } from './VoiceMessage';

export function EditorIcon({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <Button tone="quiet" className={'admin-icon ' + className} aria-label={label} title={label} {...props}>{children}</Button>;
}

export function AdminEditor({ editor, expanded, onActivate, onAttach, tools, pageTitle = 'Главная', preview = true,
  onCollapse, onExit, viewNotice, onOpenPublished, loadingPublished, recoveryError }: {
  editor: AdminEditorController; expanded: boolean; onActivate: () => void; onAttach: () => void; tools: ReactNode;
  pageTitle?: string; preview?: boolean; onCollapse?: () => void; onExit?: () => void; viewNotice?: string;
  onOpenPublished?: () => void; loadingPublished?: boolean; recoveryError?: string;
}) {
  const input = useRef<HTMLTextAreaElement>(null), menu = useRef<HTMLDivElement>(null), menuButton = useRef<HTMLButtonElement>(null);
  const header = useRef<HTMLElement>(null);
  const [large, setLarge] = useState(false), [menuOpen, setMenuOpen] = useState(false);
  const focusPending = useRef(expanded);
  const voice = editor.voice, recording = voice.phase === 'recording', requesting = voice.phase === 'requesting';
  useLayoutEffect(() => {
    const element = header.current!;
    const measure = () => element.parentElement?.style.setProperty('--admin-editor-header-height', `${element.getBoundingClientRect().height}px`);
    const observer = new ResizeObserver(measure); observer.observe(element); measure();
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => { focusPending.current = expanded; if (!expanded) setMenuOpen(false); }, [expanded]);
  useLayoutEffect(() => {
    if (expanded && editor.ready && focusPending.current) { input.current?.focus({ preventScroll: true }); focusPending.current = false; }
  }, [expanded, editor.ready]);
  useLayoutEffect(() => { if (input.current) input.current.style.height = ''; }, [large]);
  useEffect(() => {
    if (!menuOpen) return;
    menu.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const outside = (event: PointerEvent) => { if (!menu.current?.contains(event.target as Node) && !menuButton.current?.contains(event.target as Node)) setMenuOpen(false); };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [menuOpen]);
  const closeMenu = () => { setMenuOpen(false); menuButton.current?.focus(); };
  const status = preview ? <span className="admin-editor-version">Предпросмотр{editor.candidate && <>
    <span aria-hidden="true"> · </span>{editor.candidate.published
      ? <span className="admin-editor-applied"><Check aria-hidden="true" />Применено</span>
      : <span className="admin-editor-draft">Черновик</span>}</>}</span> : <span className="admin-editor-version">Открыть предпросмотр</span>;
  return <section className={'admin-editor' + (expanded ? ' admin-editor--expanded' : '')} aria-label="Редактор страницы">
    <header ref={header} className="admin-editor-header">
      {expanded ? <div className="admin-editor-heading"><strong title={`Редактор · ${pageTitle}`}>Редактор · {pageTitle}</strong>{status}</div>
        : <button type="button" className="admin-editor-open" onClick={onActivate} aria-label="Открыть редактор"><strong>Редактор · {pageTitle}</strong>{status}</button>}
      {tools}
      <EditorIcon label={expanded ? 'Свернуть редактор' : 'Развернуть редактор'} onClick={expanded ? onCollapse : onActivate}>
        {expanded ? <ChevronDown aria-hidden="true" /> : <ChevronUp aria-hidden="true" />}
      </EditorIcon>
    </header>
    {expanded && <>
      <div className="admin-editor-content">
        <div className="admin-editor-answer" aria-live="polite">
          {editor.latest?.final ? editor.latest.final.content_html !== undefined
            ? <div className="rich-content" dangerouslySetInnerHTML={{ __html: editor.latest.final.content_html }} />
            : <p>{editor.latest.final.content}</p> : editor.latest?.progress ? <p>{editor.latest.progress.content}</p> : null}
          {editor.active && <div className="admin-editor-task">
            {!editor.latest?.progress && <p>Агент работает…</p>}
            <div className="admin-editor-actions">
              <Button tone="secondary" disabled={!editor.ready || editor.busy || recording || requesting} onClick={() => void editor.control('cancel')}>
                <Square aria-hidden="true" />{editor.latest?.cancel_button_text ?? editor.latest?.progress?.cancel_button_text ?? 'Прервать задачу'}
              </Button>
              {editor.latest?.resume_allowed && !editor.latest.final && !editor.state?.local_polling &&
                <Button tone="secondary" disabled={!editor.ready || editor.busy} onClick={() => void editor.control('resume')}>Продолжить ожидание</Button>}
            </div>
          </div>}
          {editor.notice && <p role="status">{editor.notice}</p>}
          {viewNotice && <p role="status">{viewNotice}</p>}
        </div>
        {editor.canApply && <div className="admin-editor-apply"><Button onClick={() => void editor.apply()}>Применить</Button></div>}
        {(editor.actionError || editor.uncertain) && <div className="admin-editor-error" role="alert">
          {editor.actionError && <p>{editor.actionError}</p>}
          {editor.uncertain && <p>Не удалось подтвердить, принят ли запрос. Проверьте его состояние. Повторная отправка может создать ещё одну задачу.</p>}
          <div className="admin-editor-actions">
            {editor.uncertain && <Button tone="secondary" disabled={editor.busy || editor.refreshing} onClick={() => void editor.refresh()}>Проверить статус</Button>}
            {editor.canRepeat && <Button tone="secondary" onClick={() => void editor.repeat()}>{editor.uncertain ? 'Отправить повторно' : 'Повторить'}</Button>}
          </div>
        </div>}
        {editor.stateError && <div className="admin-editor-error" role="alert"><p>{editor.stateError}</p>
          <Button tone="secondary" disabled={editor.busy || editor.refreshing} onClick={() => void editor.refresh()}>Обновить статус</Button></div>}
        {(editor.candidateError || recoveryError) && <div className="admin-editor-error" role="alert"><p>{recoveryError || editor.candidateError}</p>
          <div className="admin-editor-actions">{!recoveryError && <Button tone="secondary" disabled={editor.busy || editor.refreshing || loadingPublished} onClick={() => void editor.refresh()}>Обновить предпросмотр</Button>}
            {onOpenPublished && <Button tone="secondary" disabled={editor.busy || editor.refreshing || loadingPublished} onClick={onOpenPublished}>Открыть текущую версию</Button>}
          </div></div>}
      </div>
      <form className="admin-editor-compose" onSubmit={event => { event.preventDefault(); void editor.submit(); }}>
        {editor.files.length > 0 && <ul className="admin-editor-files" aria-label="Выбранные файлы">{editor.files.map((file, index) => <li key={index}>
          <span>{file.name} · {Math.max(1, Math.ceil(file.size / 1024))} КиБ</span>
          <EditorIcon label={`Удалить ${file.name}`} disabled={editor.busy || recording || requesting} onClick={() => editor.removeFile(index)}><X aria-hidden="true" /></EditorIcon>
        </li>)}</ul>}
        {voice.clip && !recording && <VoiceMessage clip={voice.clip} disabled={editor.busy} onRemove={voice.clear} />}
        {recording ? <div className="admin-voice-recording">
          <div className="admin-editor-voice"><span className="admin-record-dot" aria-hidden="true" />
            <span className="admin-voice-time">{voiceTime(voice.progress.duration)}</span><VoiceWave peaks={voice.progress.peaks} />
            <Button tone="quiet" aria-label="Завершить запись" onClick={() => voice.finish()}><Square aria-hidden="true" />Стоп</Button>
            <EditorIcon label="Удалить запись" onClick={() => voice.finish(false)}><Trash2 aria-hidden="true" /></EditorIcon>
          </div><p className="admin-voice-caption" role="status">Идёт запись. После остановки можно прослушать.</p>
        </div> : <>
          {requesting && <div className="admin-voice-request"><span role="status">Ожидание разрешения микрофона…</span>
            <Button tone="quiet" onClick={() => voice.finish(false)}>Отменить</Button></div>}
          <div className="admin-editor-input-well">
            <textarea ref={input} aria-label="Что изменить на этой странице?" placeholder="Что изменить?" value={editor.message}
              maxLength={8192} rows={large ? 5 : 2} onChange={event => editor.setMessage(event.target.value)}
              disabled={!editor.ready || editor.busy || editor.active} />
            <div className="admin-editor-tools">
              <EditorIcon label="Прикрепить файлы" disabled={!editor.canAttach} onClick={onAttach}><Paperclip aria-hidden="true" /></EditorIcon>
              <EditorIcon label="Записать голос" disabled={!editor.canRecord} onClick={editor.microphone}><Mic aria-hidden="true" /></EditorIcon>
              <button ref={menuButton} type="button" className="button button--quiet admin-icon" aria-label="Действия редактора"
                aria-expanded={menuOpen} onClick={() => setMenuOpen(value => !value)}><MoreHorizontal aria-hidden="true" /></button>
              <span className="admin-editor-spacer" />
              <EditorIcon type="submit" label="Отправить" className="admin-editor-send" disabled={!editor.canSubmit}><ArrowUp aria-hidden="true" /></EditorIcon>
            </div>
          </div>
        </>}
        {voice.notice && <p className="admin-voice-caption" role="status">{voice.notice}</p>}
        {voice.error && <div className="admin-editor-input-error" role="alert"><p>{voice.error}</p><div className="admin-editor-actions">
          <Button tone="quiet" disabled={!editor.canRecord} onClick={editor.microphone}>Проверить снова</Button>
          <Button tone="quiet" onClick={() => { voice.dismissError(); input.current?.focus(); }}>Написать текстом</Button>
        </div></div>}
        {editor.inputError && <p className="admin-editor-input-error" role="alert">{editor.inputError}</p>}
      </form>
      {menuOpen && <div ref={menu} className="admin-editor-menu" aria-label="Действия редактора" onKeyDown={event => {
        if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenu(); }
      }}>
        <Button tone="quiet" disabled={editor.busy || editor.refreshing} onClick={() => { closeMenu(); void editor.refresh(); }}><RefreshCw aria-hidden="true" />Обновить статус</Button>
        <Button tone="quiet" disabled={!editor.ready || editor.busy || editor.active || editor.uncertain || recording || requesting}
          onClick={() => { closeMenu(); void editor.control('new-chat'); }}><Plus aria-hidden="true" />Новый чат</Button>
        <Button tone="quiet" onClick={() => { closeMenu(); setLarge(value => !value); input.current?.focus(); }}><Expand aria-hidden="true" />{large ? 'Компактное поле' : 'Увеличить поле'}</Button>
        {onExit && <Button tone="quiet" onClick={() => { closeMenu(); onExit(); }}><LogOut aria-hidden="true" />Выйти из предпросмотра</Button>}
      </div>}
    </>}
  </section>;
}
