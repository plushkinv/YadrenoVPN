import { useState } from 'react';
import { ArrowUpRight, Check, Copy, Download, QrCode, ShieldCheck, Smartphone } from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { Button, Dialog, PageHeading, RowButton } from '../components/Ui';
import { ru } from '../i18n/ru';
import type { ClientView, SubscriptionView } from '../model';

export function Connect({ subscription, client, accessUrl, onBack, onClient, onOpen, onInstall, copy }: {
  subscription: SubscriptionView; client: ClientView; accessUrl: string; onBack: () => void; onClient: () => void;
  onOpen: () => void; onInstall: () => void; copy: (value: string) => Promise<boolean>;
}) {
  const [copied, setCopied] = useState<boolean | null>(null);
  const [qr, setQr] = useState(false);
  return <><PageHeading title={ru.connectTitle} caption={ru.connectCaption} back={onBack} />
    <div className="connect-layout"><section className="panel steps-card">
      <RowButton icon={<Smartphone size={23} />} title={client.name} caption={subscription.name} onClick={onClient} trailing={<span className="text-link">{ru.change}</span>} />
      <ol className="connection-steps">
        <li><span className="step-number">1</span><div><h2>{ru.stepInstall}</h2><p>{ru.stepInstallCaption}</p><Button tone="secondary" onClick={onInstall}><Download size={18} />{ru.installClient(client.name)}</Button></div></li>
        <li><span className="step-number">2</span><div><h2>{ru.stepAdd}</h2><p>{ru.stepAddCaption}</p><Button onClick={onOpen} data-ui="connect.import">{ru.openClient(client.name)}<ArrowUpRight size={19} /></Button></div></li>
        <li><span className="step-number">3</span><div><h2>{ru.stepConnect}</h2><p>{ru.stepConnectCaption}</p></div></li>
      </ol>
    </section><aside className="panel fallback-card"><span className="icon-box"><Copy size={22} /></span><h2>{ru.fallback}</h2><p>{ru.fallbackCaption}</p>
      <label htmlFor="access-url">{ru.linkLabel}</label><input id="access-url" readOnly value={accessUrl} onFocus={event => event.target.select()} spellCheck={false} />
      <Button tone="secondary" onClick={async () => setCopied(await copy(accessUrl))}>{copied ? <Check size={18} /> : <Copy size={18} />}{copied ? ru.copied : ru.copy}</Button>
      <Button tone="quiet" onClick={() => setQr(true)}><QrCode size={19} />{ru.qr}</Button>
      {copied === false && <p role="status">{ru.copyFailed}</p>}
      <p className="privacy-note"><ShieldCheck size={17} />{ru.privateLink}</p>
    </aside></div>
    {qr && <Dialog title={ru.qrTitle} onClose={() => setQr(false)}><p className="muted">{subscription.name}</p><div className="qr-frame"><QRCodeSVG value={accessUrl} size={208} marginSize={2} title={ru.linkLabel} /></div><p className="privacy-note">{ru.privateLink}</p></Dialog>}
  </>;
}
