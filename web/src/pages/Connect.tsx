import { useState } from 'react';
import { ArrowUpRight, Check, Copy, Download, QrCode, Smartphone } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { Button, Dialog, PageHeading, RowButton } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { ClientView, SubscriptionView } from '../model';

export function Connect({ subscription, client, accessUrl, onBack, onClient, onOpen, onInstall, copy, canImport = true, importing = false }: {
  subscription: SubscriptionView; client: ClientView; accessUrl: string; onBack: () => void; onClient: () => void;
  onOpen: () => void; onInstall: (url?: string) => void; copy: (value: string) => Promise<boolean>; canImport?: boolean; importing?: boolean;
}) {
  const [copied, setCopied] = useState<boolean | null>(null);
  const [qr, setQr] = useState(false);
  return <><PageHeading title={client.name} caption={ru.connectionHelp + ' · ' + subscription.name} back={onBack} autoFocus />
    <div className="connect-layout"><section className="panel steps-card">
      <RowButton icon={<Smartphone size={23} />} title={client.name} caption={client.platforms} onClick={onClient} trailing={<span className="text-link">{ru.change}</span>} />
      <ol className="connection-steps">
        <li><span className="step-number">1</span><div><h2>{ru.stepInstall}</h2><p>{client.description ?? ru.stepInstallCaption}</p>
          <div className="action-list">{client.installLinks?.length ? client.installLinks.map(link =>
            <Button key={link.url} tone="secondary" onClick={() => onInstall(link.url)}><Download size={18} aria-hidden="true" />{link.label}</Button>)
            : <Button tone="secondary" onClick={() => onInstall()}><Download size={18} aria-hidden="true" />{ru.installClient(client.name)}</Button>}</div>
        </div></li>
        <li><span className="step-number">2</span><div><h2>{ru.stepAdd}</h2><p>{canImport ? ru.stepAddCaption : ru.stepManualCaption}</p>
          {canImport ? <Button onClick={onOpen} disabled={importing} data-ui="connect.import">{ru.openClient(client.name)}<ArrowUpRight size={19} /></Button>
            : <Button onClick={async () => setCopied(await copy(accessUrl))} data-ui="connect.import">{copied ? <Check size={18} /> : <Copy size={18} />}{copied ? ru.copied : ru.copy}</Button>}
        </div></li>
        <li><span className="step-number">3</span><div><h2>{ru.stepConnect}</h2><p>{ru.stepConnectCaption}</p></div></li>
      </ol>
    </section><aside className="panel fallback-card"><h2>{ru.manualConnection}</h2><p>{ru.fallbackCaption}</p>
      <label htmlFor="access-url">{ru.linkLabel}</label><input id="access-url" readOnly value={accessUrl} onFocus={event => event.target.select()} spellCheck={false} />
      <Button tone="secondary" onClick={async () => setCopied(await copy(accessUrl))}>{copied ? <Check size={18} /> : <Copy size={18} />}{copied ? ru.copied : ru.copy}</Button>
      <Button tone="secondary" onClick={() => setQr(true)}><QrCode size={19} />{ru.qr}</Button>
      {copied === false && <p role="status">{ru.copyFailed}</p>}
      <p className="privacy-note">{ru.privateLink}</p>
    </aside></div>
    {qr && <Dialog title={ru.qrTitle} onClose={() => setQr(false)}><p className="muted">{subscription.name}</p><div className="qr-frame"><QRCodeSVG value={accessUrl} size={208} marginSize={2} title={ru.linkLabel} /></div><p className="privacy-note">{ru.privateLink}</p></Dialog>}
  </>;
}
