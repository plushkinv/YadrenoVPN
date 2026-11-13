import { text as _tr } from "../i18n/runtime";
import type { ClientView } from '../model';
export const clients: (ClientView & { install: string; scheme?: (url: string) => string })[] = [
  { id: 'happ', name: 'Happ', platforms: 'Android, iOS, Windows, macOS, Linux, Android TV, Apple TV', get description() { return _tr("clients.1"); }, install: 'https://www.happ.su/main/ru', scheme: url => 'happ://add/' + url },
  { id: 'incy', name: 'INCY', platforms: 'Android, iOS, Windows, macOS, Linux, Android TV, Apple TV', get description() { return _tr("clients.2"); }, install: 'https://incy.cc/', scheme: url => 'incy://import/' + url },
  { id: 'v2raytun', name: 'v2RayTun', platforms: 'Android, iOS, Windows, macOS', get description() { return _tr("clients.3"); }, install: 'https://v2raytun.com/', scheme: url => 'v2raytun://import/' + url },
  { id: 'hiddify', name: 'Hiddify', platforms: 'Android, iOS, Windows, macOS, Linux', get description() { return _tr("clients.4"); }, install: 'https://hiddify.com/app/', scheme: url => 'hiddify://import/' + url },
  { id: 'v2rayng', name: 'v2rayNG', platforms: 'Android', get description() { return _tr("clients.5"); }, install: 'https://github.com/2dust/v2rayNG/releases/latest', scheme: url => 'v2rayng://install-config?url=' + encodeURIComponent(url) },
  { id: 'v2rayn', name: 'v2rayN', platforms: 'Windows, macOS, Linux', get description() { return _tr("clients.6"); }, install: 'https://github.com/2dust/v2rayN/releases/latest' },
  { id: 'streisand', name: 'Streisand', platforms: 'iOS, macOS', get description() { return _tr("clients.7"); }, install: 'https://apps.apple.com/app/streisand/id6450534064', scheme: url => 'streisand://import/' + url },
  { id: 'shadowrocket', name: 'Shadowrocket', platforms: 'iOS, macOS, Apple TV', get description() { return _tr("clients.8"); }, install: 'https://apps.apple.com/app/shadowrocket/id932747118', scheme: url => 'sub://' + btoa(new URL(url).href) },
  { id: 'exclave', name: 'Exclave', platforms: 'Android', get description() { return _tr("clients.9"); }, install: 'https://github.com/ExclaveNetwork/Exclave/releases', scheme: url => 'exclave://subscription?url=' + encodeURIComponent(url) },
  { id: 'v2box', name: 'V2Box', platforms: 'Android, iOS, macOS', get description() { return _tr("clients.10"); }, install: 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690', installLinks: [
    { label: 'Google Play · Android', url: 'https://play.google.com/store/apps/details?id=dev.hexasoftware.v2box' },
    { label: 'App Store · iOS, macOS', url: 'https://apps.apple.com/app/v2box-v2ray-client/id6446814690' },
  ], scheme: url => 'v2box://install-sub?url=' + encodeURIComponent(url) },
];
