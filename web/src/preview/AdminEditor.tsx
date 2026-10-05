import { useLayoutEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { ArrowUp, GripHorizontal, Mic, Paperclip, Square, X } from 'lucide-react';
import { Button } from '../components/Ui';
import type { AdminEditorController } from './useAdminEditor';

export function EditorIcon({ label, children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return <Button tone="quiet" className={'admin-icon ' + className} aria-label={label} title={label} {...props}>{children}</Button>;
}

export function AdminEditor({ editor, expanded, onActivate, onAttach, tools }: {
  editor: AdminEditorController; expanded: boolean; onActivate: () => void; onAttach: () => void; tools: ReactNode;
}) {
  const input = useRef<HTMLTextAreaElement>(null);
  const [height, setHeight] = useState(84);
  const drag = useRef<{ y: number; height: number } | null>(null);
  const focusPending = useRef(expanded);
  useLayoutEffect(() => { focusPending.current = expanded; }, [expanded]);
  useLayoutEffect(() => {
    if (expanded && editor.ready && focusPending.current) { input.current?.focus(); focusPending.current = false; }
  }, [expanded, editor.ready]);
  const resize = (value: number) => setHeight(Math.max(84, Math.min(value, (window.visualViewport?.height ?? window.innerHeight) * .4)));
  return <section className={'admin-editor' + (expanded ? ' admin-editor--expanded' : '')} aria-label="Кастомизатор">
    <form className="admin-editor-compose" onSubmit={event => { event.preventDefault(); void editor.submit(); }}>
      <EditorIcon label={editor.recording ? 'Завершить запись' : 'Записать голос'} disabled={!editor.canRecord}
        className={editor.recording ? 'admin-icon--recording' : ''} onClick={() => { onActivate(); void editor.microphone(); }}>
        {editor.recording ? <Square /> : <Mic />}
      </EditorIcon>
      <EditorIcon label="Прикрепить файлы" disabled={!editor.canAttach} onClick={onAttach}><Paperclip /></EditorIcon>
      <textarea ref={input} aria-label="Что изменить на этой странице?" placeholder="Что изменить?" value={editor.message}
        maxLength={8192} rows={expanded ? 3 : 1} style={expanded ? { height } : undefined}
        onFocus={() => { if (!expanded) onActivate(); }} onChange={event => editor.setMessage(event.target.value)}
        disabled={expanded && (!editor.ready || editor.busy || editor.active || editor.recording)} />
      {expanded && <EditorIcon label="Изменить высоту поля" className="admin-editor-resize"
        onPointerDown={event => { drag.current = { y: event.clientY, height }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={event => { if (drag.current) resize(drag.current.height + event.clientY - drag.current.y); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
        onKeyDown={event => { if (event.key === 'ArrowUp' || event.key === 'ArrowDown') { event.preventDefault(); resize(height + (event.key === 'ArrowDown' ? 24 : -24)); } }}><GripHorizontal /></EditorIcon>}
      {expanded && <span className="admin-editor-spacer" />}
      {tools}
      {expanded && <EditorIcon type="submit" label="Отправить" className="admin-editor-send" disabled={!editor.canSubmit}><ArrowUp /></EditorIcon>}
    </form>
    {expanded && <>
      <div className="admin-editor-actions">
        <Button tone="secondary" disabled={editor.busy} onClick={() => void editor.refresh()}>Ну чё там?</Button>
        <Button tone="secondary" disabled={!editor.ready || editor.busy || editor.active || editor.recording} onClick={() => void editor.control('new-chat')}>Новый чат</Button>
        <Button tone="secondary" disabled={!editor.active || !editor.ready || editor.busy} onClick={() => void editor.control('cancel')}>{editor.latest?.cancel_button_text ?? editor.latest?.progress?.cancel_button_text ?? 'Прервать задачу'}</Button>
      </div>
      <div className="admin-editor-content">
        {editor.files.length > 0 && <ul className="admin-editor-files" aria-label="Выбранные файлы">{editor.files.map((file, index) => <li key={index}>
          <span>{file.name} · {Math.max(1, Math.ceil(file.size / 1024))} КиБ</span>
          <EditorIcon label={`Удалить ${file.name}`} disabled={editor.busy || editor.recording} onClick={() => editor.removeFile(index)}><X /></EditorIcon>
        </li>)}</ul>}
        {editor.recording && <p role="status">Идёт запись…</p>}
        {editor.voice && <div className="admin-editor-voice"><span>Голосовое сообщение готово</span><EditorIcon label="Удалить запись" disabled={editor.busy} onClick={editor.removeVoice}><X /></EditorIcon></div>}
        <div className="admin-editor-answer" aria-live="polite">
          {editor.latest?.final ? editor.latest.final.content_html !== undefined
            ? <div className="rich-content" dangerouslySetInnerHTML={{ __html: editor.latest.final.content_html }} />
            : <p>{editor.latest.final.content}</p> : editor.latest?.progress ? <p>{editor.latest.progress.content}</p> : null}
          {editor.notice && <p>{editor.notice}</p>}
          {editor.error && <p role="alert">{editor.error}</p>}
        </div>
        {editor.latest?.resume_allowed && !editor.latest.final && !editor.state?.local_polling && <div className="button-row">
          <Button tone="secondary" disabled={!editor.ready || editor.busy} onClick={() => void editor.control('resume')}>Продолжить ожидание</Button>
        </div>}
      </div>
    </>}
  </section>;
}
