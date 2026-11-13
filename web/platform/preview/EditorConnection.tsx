import { text as _tr } from "../i18n/runtime";
import { useEffect, useId, useRef, useState } from 'react';
import { WifiOff } from 'lucide-react';

export function EditorConnection() {
  const root = useRef<HTMLDivElement>(null), id = useId();
  const [hovered, setHovered] = useState(false), [focused, setFocused] = useState(false);
  const [pinned, setPinned] = useState(false), [dismissed, setDismissed] = useState(false);
  const open = !dismissed && (hovered || focused || pinned);
  const close = () => { setPinned(false); setDismissed(true); };
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) close(); };
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape, true);
    window.addEventListener('blur', close);
    return () => {
      document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape, true);
      window.removeEventListener('blur', close);
    };
  }, [open]);
  return <div ref={root} className="admin-editor-connection">
    <button type="button" className="admin-icon admin-connection-icon" aria-label={_tr("EditorConnection.1")}
      aria-describedby={open ? id : undefined} aria-expanded={open}
      onPointerEnter={event => { if (event.pointerType === 'mouse') { setHovered(true); setDismissed(false); } }}
      onPointerLeave={() => setHovered(false)}
      onFocus={() => { setFocused(true); setDismissed(false); }}
      onBlur={() => { setFocused(false); close(); }}
      onClick={() => { setPinned(!pinned); setDismissed(pinned); }}>
      <WifiOff aria-hidden="true" />
    </button>
    {open && <div id={id} role="tooltip" className="admin-connection-tooltip">
      {_tr("EditorConnection.2")}</div>}
  </div>;
}
