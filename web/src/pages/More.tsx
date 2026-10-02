import { LifeBuoy, Palette, UserRound } from 'lucide-react';
import { PageHeading, RowButton } from '../components/Ui';
import { ru } from '../i18n/ru';

export function More({ accountLabel, appearance, help }: { accountLabel: string; appearance: () => void; help: () => void }) {
  return <><PageHeading title={ru.more} caption={ru.moreCaption} /><section className="panel more-list">
    <div className="account-summary"><span className="icon-box"><UserRound size={24} /></span><div><strong>{accountLabel}</strong><p>{ru.accountCaption}</p></div></div>
    <RowButton icon={<Palette size={23} />} title={ru.appearance} caption={ru.appearanceCaption} onClick={appearance} />
    <RowButton icon={<LifeBuoy size={23} />} title={ru.help} caption={ru.quickHelpCaption} onClick={help} />
  </section></>;
}
