// @ts-check
/** Stylised phone illustration (pure CSS/SVG) and battery gauge for the devices control panel. */
import { html } from '/vendor/preact-htm.js';
import { Icon } from './icons.js';
import { cx } from './ui.js';
import { clip } from '../lib/format.js';

/** @param {{level: number|null|undefined, charging?: boolean}} p */
export function BatteryGauge({ level }) {
  if (level == null) return html`<span class="battery is-unknown" title="电量未知"><${Icon} name="battery" size=${18} /><span>—</span></span>`;
  const pct = Math.max(0, Math.min(100, level));
  const tone = pct < 15 ? 'danger' : pct < 35 ? 'warn' : 'ok';
  return html`<span class=${cx('battery', `tone-${tone}`)} role="meter" aria-label="电量" aria-valuenow=${pct} aria-valuemin="0" aria-valuemax="100">
    <svg width="30" height="16" viewBox="0 0 30 16" aria-hidden="true">
      <rect x="1" y="1" width="25" height="14" rx="3.5" fill="none" stroke="currentColor" stroke-opacity=".5" stroke-width="1.5" />
      <rect x="27" y="5" width="2" height="6" rx="1" fill="currentColor" fill-opacity=".5" />
      <rect x="3" y="3" width=${Math.max(1.5, (21 * pct) / 100)} height="10" rx="2" fill="currentColor" />
    </svg><span>${pct}%</span></span>`;
}

/** Friendly app name from a package id. @param {string|null|undefined} pkg */
export function appName(pkg) {
  if (!pkg) return '';
  const known = { 'com.tencent.mm': '微信', 'com.tencent.mobileqq': 'QQ', 'com.ss.android.ugc.aweme': '抖音', 'tv.danmaku.bili': '哔哩哔哩',
    'com.eg.android.AlipayGphone': '支付宝', 'com.taobao.taobao': '淘宝', 'com.android.chrome': 'Chrome', 'com.xingin.xhs': '小红书',
    'com.android.settings': '设置', 'com.google.android.youtube': 'YouTube', 'com.sina.weibo': '微博', 'com.zhihu.android': '知乎' };
  return known[pkg] || pkg.split('.').slice(-1)[0];
}

/** @param {{device: any, size?: 'sm'|'md'}} p */
export function Phone({ device, size = 'md' }) {
  const d = device || {};
  const agent = d.role === 'agent';
  return html`<div class=${cx('phone', `phone-${size}`, d.online ? 'is-online' : 'is-offline', agent ? 'is-agent' : 'is-personal')} aria-hidden="true">
    ${d.online ? html`<span class="phone-pulse"></span>` : null}
    <div class="phone-frame">
      <span class="phone-notch"></span>
      <div class="phone-screen">
        <div class="phone-status">
          <span>${d.network ? (d.network === 'wifi' ? html`<${Icon} name="wifi" size=${10} />` : d.network) : ''}</span>
          <span>${d.battery != null ? `${d.battery}%` : ''}</span>
        </div>
        <div class="phone-content">
          ${d.online ? html`
            <span class="phone-glyph">${agent ? '🐋' : html`<${Icon} name="eye" size=${size === 'sm' ? 14 : 22} />`}</span>
            <span class="phone-app">${d.foreground ? clip(appName(d.foreground), 10) : agent ? '待命' : '在线'}</span>`
            : html`<span class="phone-off"><${Icon} name="power" size=${size === 'sm' ? 14 : 20} /></span>`}
        </div>
        <span class="phone-home"></span>
      </div>
    </div>
  </div>`;
}
