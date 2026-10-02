import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ChevronRight, X } from 'lucide-react';
import { ru } from '../i18n/ru';
import { replaceable } from '../runtime/overrides';

function BaseButton({ children, tone = 'primary', className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: 'primary' | 'secondary' | 'quiet' }) {
  return <button type="button" className={`button button--${tone} ${className}`} {...props}>{children}</button>;
}

function BasePageHeading({ title, caption, back, action }: { title: string; caption?: string; back?: () => void; action?: ReactNode }) {
  return <div className="page-heading">
    {back && <Button tone="quiet" onClick={back} className="back-button"><ArrowLeft size={18} />{ru.back}</Button>}
    <div className="heading-row"><div><h1>{title}</h1>{caption && <p>{caption}</p>}</div>{action}</div>
  </div>;
}

function BaseBadge({ children, tone = 'success' }: { children: ReactNode; tone?: 'success' | 'warning' | 'neutral' }) {
  return <span className={`badge badge--${tone}`}><span className="status-dot" />{children}</span>;
}

function BaseRowButton({ icon, title, caption, onClick, trailing }: { icon: ReactNode; title: string; caption?: string; onClick: () => void; trailing?: ReactNode }) {
  return <button className="row-button" type="button" onClick={onClick}>
    <span className="icon-box">{icon}</span><span className="row-text"><strong>{title}</strong>{caption && <span>{caption}</span>}</span>
    {trailing ?? <ChevronRight size={20} className="muted" />}
  </button>;
}

function BaseDialog({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  return <dialog ref={dialog} className="dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onKeyDown={event => {
      if (event.key !== 'Tab') return;
      const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter(item => item.getClientRects().length > 0);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="dialog-body"><div className="dialog-heading"><h2 id={titleId}>{title}</h2>
      <Button tone="quiet" className="icon-button" onClick={onClose} aria-label={ru.close}><X size={21} /></Button>
    </div>{children}</div>
  </dialog>;
}

function BaseCheckList({ items }: { items: readonly string[] }) {
  return <ul className="check-list">{items.map(item => <li key={item}><Check size={18} /><span>{item}</span></li>)}</ul>;
}

export function ExternalArrow() { return <ArrowUpRight size={20} aria-hidden="true" />; }

export const Button = replaceable('button', BaseButton);
export const PageHeading = replaceable('page.heading', BasePageHeading);
export const Badge = replaceable('badge', BaseBadge);
export const RowButton = replaceable('row.button', BaseRowButton);
export const Dialog = replaceable('dialog', BaseDialog);
export const CheckList = replaceable('check.list', BaseCheckList);
