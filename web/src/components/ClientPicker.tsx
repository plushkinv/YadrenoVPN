import { useState } from 'react';
import { Check, ChevronRight } from 'lucide-react';
import { Button, Dialog } from './Ui';
import { Field } from './Forms';
import { clients } from '../runtime/environment';
import { ru } from '../i18n/ru';
import { appText as t } from '../i18n/app';

function devicePlatform() {
  const ua = navigator.userAgent;
  if (/Android/i.test(ua)) return 'Android';
  if (/iPhone|iPad|iPod/i.test(ua) || /Macintosh/i.test(ua) && navigator.maxTouchPoints > 1) return 'iOS';
  if (/Windows/i.test(ua)) return 'Windows';
  if (/Mac/i.test(ua)) return 'macOS';
  if (/Linux/i.test(ua)) return 'Linux';
  return '';
}

export function ClientPicker({ selected, onSelect, onClose }: {
  selected: string; onSelect: (id: string) => void; onClose: () => void;
}) {
  const [platform, setPlatform] = useState(() => {
    const detected = devicePlatform();
    const supported = clients.find(client => client.id === selected)?.platforms.split(',').map(value => value.trim());
    return detected && supported && !supported.includes(detected) ? supported[0] : detected;
  });
  const platforms = [...new Set(clients.flatMap(client => client.platforms.split(',').map(value => value.trim())))];
  const visible = clients.filter(client => !platform || client.platforms.split(',').some(value => value.trim() === platform));
  return <Dialog title={t.chooseClient} onClose={onClose}>
    <div className="client-picker" data-ui="client.picker">
      <Field label={ru.clientDevice}><select value={platform} onChange={event => setPlatform(event.target.value)}>
        <option value="">{ru.allDevices}</option>
        {platforms.map(value => <option key={value} value={value}>{value}</option>)}
      </select></Field>
      <div className="client-picker-list">
        {visible.map(client => <Button key={client.id} tone="secondary" className="client-option"
          aria-pressed={client.id === selected} onClick={() => { onSelect(client.id); onClose(); }}>
          <span className="client-option-icon" aria-hidden="true">{client.name.slice(0, 2)}</span>
          <span className="client-option-copy"><strong>{client.name}</strong>
            <span>{client.id === clients[0].id ? ru.defaultClient : client.platforms}</span>
          </span>
          {client.id === selected ? <Check size={20} aria-hidden="true" /> : <ChevronRight size={20} aria-hidden="true" />}
        </Button>)}
      </div>
    </div>
  </Dialog>;
}
