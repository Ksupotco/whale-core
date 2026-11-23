import { useCallback, useEffect, useState, type FormEvent } from 'react';
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  Cpu,
  Glasses,
  LoaderCircle,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Smartphone,
  Trash2,
  TriangleAlert,
  Watch,
} from 'lucide-react';
import {
  deviceCommand,
  fetchDevice,
  fetchDeviceReceipts,
  fetchDevices,
  fetchDiagnostics,
  revokeDevice,
  type DeviceReceipt,
  type DeviceView,
  type DevicesReport,
  type DiagnosticsReport,
} from './api';
import { relativeTime } from './data';
import { Tag } from './ds';
import { Empty, Modal, PageHeading } from './ui';
import { useWorkspace } from './workspace';

// V2 能力枚举（contracts/v2 §5.1，与服务端单一事实源一致；本地模式与兜底用）
export const DEVICE_CAPABILITIES = [
  'screen.context.read',
  'screen.history.search',
  'screen.evidence.read',
  'screen.live.subscribe',
  'phone.notification.post',
  'phone.notification.read',
  'phone.call.ring',
  'phone.call.audio',
  'phone.device.safe_action',
  'glasses.card.show',
  'glasses.card.update',
  'glasses.card.dismiss',
  'device.observe',
  'device.tap',
  'device.long_press',
  'device.swipe',
  'device.input_text',
  'device.key',
  'device.wait',
  'device.app.launch',
  'device.app.list',
  'device.app.install',
  'device.app.uninstall',
  'device.shell',
  'device.file.read',
  'device.file.write',
  'device.file.list',
  'device.logcat',
  'device.stop',
  'device.output.read',
  'device.screen.record',
  'device.status',
];
const CAPABILITY_LABELS: Record<string, string> = {
  'screen.context.read': '读取当前屏幕',
  'screen.history.search': '搜索屏幕历史',
  'screen.evidence.read': '读取屏幕证据',
  'screen.live.subscribe': '订阅屏幕实时流',
  'phone.notification.post': '发送手机通知',
  'phone.notification.read': '读取手机通知',
  'phone.call.ring': '让手机响铃',
  'phone.call.audio': '通话音频',
  'phone.device.safe_action': '手机安全动作',
  'glasses.card.show': '显示眼镜卡片',
  'glasses.card.update': '更新眼镜卡片',
  'glasses.card.dismiss': '关闭眼镜卡片',
  'device.observe': '观察设备状态',
  'device.tap': '点按屏幕',
  'device.long_press': '长按屏幕',
  'device.swipe': '滑动屏幕',
  'device.input_text': '输入文本',
  'device.key': '发送按键',
  'device.wait': '等待',
  'device.app.launch': '启动应用',
  'device.app.list': '列出应用',
  'device.app.install': '安装应用',
  'device.app.uninstall': '卸载应用',
  'device.shell': '执行 Shell',
  'device.file.read': '读取文件',
  'device.file.write': '写入文件',
  'device.file.list': '列出文件',
  'device.logcat': '读取日志',
  'device.stop': '停止动作',
  'device.output.read': '读取输出',
  'device.screen.record': '录制屏幕',
  'device.status': '设备状态查询',
};
const FAMILY_NAMES: Record<string, string> = {
  screen: '屏幕感知',
  phone: '手机能力',
  glasses: '眼镜能力',
  device: '设备控制',
};
function capabilityFamilies(all: string[]): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const cap of all) {
    const family = cap.split('.')[0] || 'device';
    if (!groups.has(family)) groups.set(family, []);
    groups.get(family)!.push(cap);
  }
  return [...groups.entries()];
}
const asMs = (v: number | null | undefined) =>
  typeof v === 'number' && v > 0 ? (v > 1e12 ? v : v * 1000) : null;
export function deviceStatus(device: DeviceView): { text: string; tone: string } {
  if (device.status === 'pending') return { text: '待配对', tone: 'amber' };
  if (device.status === 'paused') return { text: '已暂停', tone: 'amber' };
  if (device.status === 'revoked') return { text: '已吊销', tone: 'slate' };
  return device.online ? { text: '在线', tone: 'mint' } : { text: '离线', tone: 'slate' };
}
function deviceIcon(device: DeviceView) {
  const key = `${device.device_id} ${device.label}`.toLowerCase();
  if (key.includes('rayneo') || key.includes('glasses') || key.includes('眼镜')) return Glasses;
  if (key.includes('watch') || key.includes('手表')) return Watch;
  return Smartphone;
}
function receiptTone(state: string): string {
  if (['succeeded', 'delivered', 'acked'].includes(state)) return 'mint';
  if (['failed', 'denied'].includes(state)) return 'danger';
  if (['unknown', 'expired'].includes(state)) return 'amber';
  return 'blue';
}
const RECEIPT_STATE: Record<string, string> = {
  pending: '在飞',
  sent: '已下发',
  delivered: '已送达',
  acked: '已确认',
  succeeded: '成功',
  failed: '失败',
  denied: '被拒绝',
  expired: '已过期',
  unknown: '未知',
};

export default function DevicesPage() {
  const { connection, notify } = useWorkspace();
  const [report, setReport] = useState<DevicesReport | null>(null);
  const [diag, setDiag] = useState<DiagnosticsReport | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<DeviceView | null>(null);
  const [receipts, setReceipts] = useState<DeviceReceipt[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailBusy, setDetailBusy] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [lastSync, setLastSync] = useState<number | null>(null);
  const [error, setError] = useState('');

  const devices = report?.devices ?? [];
  const capabilities = report?.capabilities ?? DEVICE_CAPABILITIES;
  const retention = report?.retention;

  const load = useCallback(
    async (quiet = false) => {
      if (!connection) return;
      if (!quiet) setLoading(true);
      setError('');
      try {
        const [r, d] = await Promise.all([
          fetchDevices(connection),
          fetchDiagnostics(connection).catch(() => null),
        ]);
        setReport(r);
        setDiag(d);
        setLastSync(Date.now());
      } catch (e) {
        setError((e as Error).message);
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [connection],
  );
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!connection || !autoRefresh) return;
    const id = setInterval(() => {
      if (!document.hidden) void load(true);
    }, 10000);
    return () => clearInterval(id);
  }, [connection, autoRefresh, load]);

  const openDetail = async (id: string) => {
    if (!connection) return;
    setSelectedId(id);
    setDetailBusy(true);
    setReceipts([]);
    try {
      const [d, r] = await Promise.all([
        fetchDevice(connection, id),
        fetchDeviceReceipts(connection, id).catch(() => ({ device_id: id, receipts: [] })),
      ]);
      setDetail(d);
      setReceipts(r.receipts);
    } catch (e) {
      notify((e as Error).message, true);
      setSelectedId(null);
    } finally {
      setDetailBusy(false);
    }
  };
  const closeDetail = () => {
    if (detailBusy) return;
    setSelectedId(null);
    setDetail(null);
  };

  const run = async (
    id: string,
    command: 'rename' | 'role' | 'pause' | 'resume' | 'scopes' | 'purge',
    body: unknown,
    done: string,
  ) => {
    if (!connection) return;
    setDetailBusy(true);
    try {
      if (command === 'purge') {
        await deviceCommand(connection, id, 'purge');
        setSelectedId(null);
        setDetail(null);
      } else {
        await deviceCommand(connection, id, command, body);
        setDetail(await fetchDevice(connection, id));
      }
      await load(true);
      notify(done);
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setDetailBusy(false);
    }
  };
  const revoke = async (id: string) => {
    if (!connection) return;
    setDetailBusy(true);
    try {
      await revokeDevice(connection, id);
      setDetail(await fetchDevice(connection, id));
      await load(true);
      notify('设备已吊销，在线连接已被踢除。');
    } catch (e) {
      notify((e as Error).message, true);
    } finally {
      setDetailBusy(false);
    }
  };

  return (
    <div className="page-enter">
      <PageHeading
        page="devices"
        action={
          <button className="btn" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={15} className={loading ? 'spin' : ''} />
            刷新
          </button>
        }
      />
      <div className="device-toolbar panel">
        <label className="auto-refresh">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(e) => setAutoRefresh(e.target.checked)}
          />
          每 10 秒自动刷新
        </label>
        <span className="muted small-text">
          最近同步 {lastSync ? new Date(lastSync).toLocaleTimeString('zh-CN', { hour12: false }) : '—'}
        </span>
      </div>
      {error && (
        <p className="inline-error" role="alert">
          {error}
        </p>
      )}

      <Topology devices={devices} onOpen={openDetail} />

      <section className="panel list-panel device-list-panel">
        <div className="panel-heading device-list-head">
          <h2>已配对设备（{devices.length}）</h2>
          <span className="muted small-text">Ed25519 · Device Protocol V2</span>
        </div>
        <div className="record-list">
          {devices.map((d) => {
            const st = deviceStatus(d);
            const Icon = deviceIcon(d);
            return (
              <button className="record-row" key={d.device_id} onClick={() => void openDetail(d.device_id)}>
                <span className={`color-icon ${st.tone === 'slate' ? 'neutral' : st.tone}`}>
                  <Icon size={17} />
                </span>
                <span className="record-info">
                  <strong>
                    {d.label || '未命名设备'}
                    {d.role ? <em className="device-role">{d.role}</em> : null}
                  </strong>
                  <small>
                    <code>{d.device_id.slice(0, 12)}…</code> · {d.scopes?.length ?? 0} 项能力授权
                  </small>
                </span>
                <span className={`tag ${st.tone}`}>
                  <i className={`dot ${d.online ? 'on' : ''}`} />
                  {st.text}
                </span>
                <ChevronRight size={15} />
              </button>
            );
          })}
          {!devices.length && (
            <Empty
              title="还没有已配对的设备"
              text="设备通过配对码完成登记后，会出现在这里。"
            />
          )}
        </div>
      </section>

      {connection && retention && retention.state !== 'unavailable' && (        <section className="panel retention-panel">
          <div className="panel-heading">
            <h2>动作记录清理</h2>
            <span className={`tag ${retention.state === 'ok' || retention.state === 'idle' ? 'mint' : 'neutral'}`}>
              {retention.state}
            </span>
          </div>
          <div className="retention-grid">
            <div>
              <span>本批扫描 / 回执</span>
              <strong>
                {retention.scanned ?? '—'} / {retention.receipt_scanned ?? '—'}
              </strong>
            </div>
            <div>
              <span>删除 / 待核对</span>
              <strong>
                {retention.deleted ?? '—'} / {retention.marked_unknown ?? '—'}
              </strong>
            </div>
          </div>
        </section>
      )}

      {diag?.memory_worker && (
        <section className="panel retention-panel">
          <div className="panel-heading">
            <h2>记忆后台任务</h2>
            <span className={`tag ${diag.memory_worker.failed ? 'danger' : 'mint'}`}>
              {diag.memory_worker.model_busy
                ? '模型调用中'
                : diag.memory_worker.in_flight
                  ? '处理中'
                  : '空闲'}
            </span>
          </div>
          <div className="retention-grid">
            <div>
              <span>队列 / 已完成</span>
              <strong>
                {diag.memory_worker.queue_depth ?? 0} / {diag.memory_worker.completed ?? 0}
              </strong>
            </div>
            <div>
              <span>重试 / 失败</span>
              <strong>
                {diag.memory_worker.retried ?? 0} / {diag.memory_worker.failed ?? 0}
              </strong>
            </div>
          </div>
        </section>
      )}

      {selectedId && (
        <DeviceDetail
          device={detail}
          busy={detailBusy}
          receipts={receipts}
          capabilities={capabilities}
          onClose={closeDetail}
          onRun={run}
          onRevoke={revoke}
        />
      )}
    </div>
  );
}

function Topology({ devices, onOpen }: { devices: DeviceView[]; onOpen: (id: string) => void }) {
  if (!devices.length) return null;
  return (
    <section className="panel topology-panel">
      <div className="panel-heading">
        <h2>设备拓扑</h2>
        <span className="muted small-text">上行感知 · 下行动作（真实计数）</span>
      </div>
      <div className="topology-body">
        <div className="topology-core">
          <span className="color-icon violet">
            <Cpu size={19} />
          </span>
          <strong>Whale Core</strong>
          <small>中枢网关</small>
        </div>
        <div className="topology-links">
          {devices.map((d) => {
            const Icon = deviceIcon(d);
            const up = d.activity?.uplink;
            const down = d.activity?.downlink;
            return (
              <button
                className={`topology-link ${d.online ? 'online' : ''}`}
                key={d.device_id}
                onClick={() => onOpen(d.device_id)}
                aria-label={`查看设备 ${d.label || d.device_id} 详情`}
              >
                <span className="topology-channel">
                  <ArrowUp size={13} />
                  上行 {up ? `${up.events} 事件` : '—'}
                  <small>{up?.last_at ? relativeTime(asMs(up.last_at) || 0) : ''}</small>
                </span>
                <span className="topology-node">
                  <Icon size={16} />
                  <span>
                    <strong>{d.label || '未命名设备'}</strong>
                    <small>{d.online ? '在线' : deviceStatus(d).text}</small>
                  </span>
                </span>
                <span className="topology-channel">
                  <ArrowDown size={13} />
                  下行 {down ? `${down.pending} 在飞` : '—'}
                  <small>
                    {down?.last_receipt
                      ? `${RECEIPT_STATE[down.last_receipt.state] || down.last_receipt.state} · ${relativeTime(asMs(down.last_receipt.updated_at) || 0)}`
                      : ''}
                  </small>
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </section>
  );
}

function DeviceDetail({
  device,
  busy,
  receipts,
  capabilities,
  onClose,
  onRun,
  onRevoke,
}: {
  device: DeviceView | null;
  busy: boolean;
  receipts: DeviceReceipt[];
  capabilities: string[];
  onClose: () => void;
  onRun: (
    id: string,
    command: 'rename' | 'role' | 'pause' | 'resume' | 'scopes' | 'purge',
    body: unknown,
    done: string,
  ) => Promise<void>;
  onRevoke: (id: string) => Promise<void>;
}) {
  const [tab, setTab] = useState('概览');
  const [editingName, setEditingName] = useState(false);
  const [editingRole, setEditingRole] = useState(false);
  const [confirm, setConfirm] = useState<'revoke' | 'purge' | 'pause' | null>(null);
  const [scopeDraft, setScopeDraft] = useState<string[] | null>(null);
  useEffect(() => {
    setTab('概览');
    setEditingName(false);
    setEditingRole(false);
    setConfirm(null);
    setScopeDraft(null);
  }, [device?.device_id]);
  if (!device) {
    return (
      <Modal title="设备详情" onClose={onClose} wide>
        <p className="muted padded">
          <LoaderCircle size={15} className="spin" /> 正在读取设备…
        </p>
      </Modal>
    );
  }
  const st = deviceStatus(device);
  const active = device.status === 'active';
  const scopes = scopeDraft ?? device.scopes ?? [];
  const scopeDirty = scopeDraft !== null;
  const saveScopes = () =>
    onRun(device.device_id, 'scopes', { scopes }, '设备能力授权已更新。');
  const submitName = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const label = String(new FormData(e.currentTarget).get('label') || '').trim();
    if (!label) return;
    void onRun(device.device_id, 'rename', { label }, '设备已重命名。');
    setEditingName(false);
  };
  const saveRole = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const role = String(new FormData(e.currentTarget).get('role') || '').trim();
    void onRun(device.device_id, 'role', { role }, role ? '设备角色已更新。' : '设备角色已清除。');
    setEditingRole(false);
  };
  return (
    <Modal title="设备详情" onClose={onClose} wide className="device-detail-modal">
      <div className="detail-title">
        <span className={`color-icon ${st.tone === 'slate' ? 'neutral' : st.tone}`}>
          {(() => {
            const Icon = deviceIcon(device);
            return <Icon size={22} />;
          })()}
        </span>
        <div>
          <small>
            <code>{device.device_id}</code>
          </small>
          <h3>{device.label || '未命名设备'}</h3>
        </div>
        <span className={`tag ${st.tone}`}>
          <i className={`dot ${device.online ? 'on' : ''}`} />
          {st.text}
        </span>
      </div>
      <div className="segment-control device-detail-tabs" role="tablist">
        {['概览', '能力授权', '回执'].map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
            {t}
            {t === '回执' && receipts.length ? `（${receipts.length}）` : ''}
          </button>
        ))}
      </div>

      {tab === '概览' && (
        <div className="device-overview">
          <div className="detail-meta">
            <span>设备标识</span>
            <code>{device.device_id}</code>
          </div>
          <div className="detail-meta">
            <span>注册状态</span>
            {device.status}
            {device.session ? ` · 会话 ${device.session.status}` : ''}
          </div>
          <div className="detail-meta">
            <span>设备角色</span>
            {device.role || <span className="muted">未设置</span>}
            <button className="text-link" onClick={() => setEditingRole((v) => !v)}>
              <Pencil size={12} />
              {device.role ? '修改' : '设置'}
            </button>
          </div>
          {editingRole && (
            <form className="inline-form" onSubmit={saveRole}>
              <input
                name="role"
                defaultValue={device.role || ''}
                maxLength={32}
                placeholder="如：日常佩戴 / 备用（留空清除）"
                aria-label="设备角色"
              />
              <button className="btn primary" disabled={busy}>
                保存
              </button>
            </form>
          )}
          <div className="detail-meta">
            <span>设备名称</span>
            {device.label || '未命名'}
            <button className="text-link" onClick={() => setEditingName((v) => !v)}>
              <Pencil size={12} />
              重命名
            </button>
          </div>
          {editingName && (
            <form className="inline-form" onSubmit={submitName}>
              <input
                name="label"
                defaultValue={device.label}
                maxLength={64}
                required
                aria-label="设备名称"
              />
              <button className="btn primary" disabled={busy}>
                保存
              </button>
            </form>
          )}
          <div className="detail-meta">
            <span>注册时间</span>
            {asMs(device.created_at)
              ? new Date(asMs(device.created_at)!).toLocaleString('zh-CN', { hour12: false })
              : '—'}
          </div>
          <div className="detail-meta">
            <span>能力授权</span>
            {device.scopes?.length ?? 0} 项
            <button className="text-link" onClick={() => setTab('能力授权')}>
              管理
              <ChevronRight size={12} />
            </button>
          </div>
          {device.activity && (
            <div className="device-activity">
              <div>
                <ArrowUp size={13} />
                上行感知 <strong>{device.activity.uplink.events}</strong> 事件
                <small>
                  {device.activity.uplink.last_at
                    ? `最近 ${relativeTime(asMs(device.activity.uplink.last_at) || 0)}`
                    : '暂无记录'}
                </small>
              </div>
              <div>
                <ArrowDown size={13} />
                下行动作 <strong>{device.activity.downlink.pending}</strong> 在飞
                <small>
                  {device.activity.downlink.last_receipt
                    ? `最近回执 ${RECEIPT_STATE[device.activity.downlink.last_receipt.state] || device.activity.downlink.last_receipt.state}`
                    : '暂无回执'}
                </small>
              </div>
            </div>
          )}
          {device.transports && Object.keys(device.transports).length > 0 && (
            <div className="detail-meta device-transports">
              <span>通道</span>
              <span className="device-transport-list">
                {Object.entries(device.transports).map(([kind, v]) => (
                  <Tag tone="blue" key={kind}>
                    {kind}：{typeof v === 'string' ? v : JSON.stringify(v)}
                  </Tag>
                ))}
              </span>
            </div>
          )}
          {device.capabilities_effective && device.capabilities_effective.length > 0 && (
            <div className="detail-meta">
              <span>有效能力</span>
              {device.capabilities_effective.length} 项（真实连接视图）
            </div>
          )}
          <div className="form-actions device-actions">
            {device.status === 'active' && (
              <button className="btn" disabled={busy} onClick={() => setConfirm('pause')}>
                <Pause size={14} />
                暂停设备
              </button>
            )}
            {device.status === 'paused' && (
              <button
                className="btn primary"
                disabled={busy}
                onClick={() => void onRun(device.device_id, 'resume', {}, '设备已恢复。')}
              >
                <Play size={14} />
                恢复设备
              </button>
            )}
            {device.status !== 'revoked' && (
              <button className="btn danger-ghost" disabled={busy} onClick={() => setConfirm('revoke')}>
                <TriangleAlert size={14} />
                吊销设备
              </button>
            )}
            {device.status === 'revoked' && (
              <button className="btn danger-ghost" disabled={busy} onClick={() => setConfirm('purge')}>
                <Trash2 size={14} />
                彻底删除
              </button>
            )}
          </div>
        </div>
      )}

      {tab === '能力授权' && (
        <div className="device-scopes">
          <p className="form-hint">
            勾选设备可调用的能力（contracts/v2 §5.1 枚举）。保存后即时生效，收窄授权是停止设备自主体的等价手段。
          </p>
          {capabilityFamilies(capabilities).map(([family, caps]) => (
            <section key={family} className="scope-family">
              <h4>{FAMILY_NAMES[family] || family}</h4>
              <div className="scope-grid">
                {caps.map((cap) => {
                  const on = scopes.includes(cap);
                  return (
                    <button
                      key={cap}
                      role="switch"
                      aria-checked={on}
                      aria-label={`${CAPABILITY_LABELS[cap] || cap}（${cap}）`}
                      className={`scope-chip ${on ? 'on' : ''}`}
                      disabled={!active && !scopeDirty}
                      onClick={() =>
                        setScopeDraft(
                          on ? scopes.filter((s) => s !== cap) : [...scopes, cap],
                        )
                      }
                    >
                      <span className="scope-check">{on ? <Check size={12} /> : null}</span>
                      <span>
                        <strong>{CAPABILITY_LABELS[cap] || cap}</strong>
                        <code>{cap}</code>
                      </span>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
          <div className="form-actions">
            <span className="muted small-text">
              已选 {scopes.length} / {capabilities.length} 项
            </span>
            <button className="btn" disabled={!scopeDirty || busy} onClick={() => setScopeDraft(null)}>
              还原
            </button>
            <button className="btn primary" disabled={!scopeDirty || busy} onClick={() => void saveScopes()}>
              {busy ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />}
              保存授权
            </button>
          </div>
        </div>
      )}

      {tab === '回执' && (
        <div className="device-receipts">
          {receipts.length > 0 && (
            <div className="receipt-table" role="table" aria-label="设备回执">
              <div className="receipt-row receipt-head" role="row">
                <span>能力 / 摘要</span>
                <span>状态</span>
                <span>通道</span>
                <span>更新时间</span>
              </div>
              {receipts.map((r) => (
                <div className="receipt-row" role="row" key={r.idempotency_key}>
                  <span className="receipt-main">
                    <code>{r.capability}</code>
                    <small>{r.summary || r.idempotency_key}</small>
                  </span>
                  <span>
                    <span className={`tag ${receiptTone(r.state)}`}>
                      {RECEIPT_STATE[r.state] || r.state}
                    </span>
                    {r.outcome_source && (
                      <small className="receipt-source" title={`判定来源：${r.outcome_source}`}>
                        via {r.outcome_source}
                      </small>
                    )}
                  </span>
                  <span className="muted small-text">{r.route}</span>
                  <span className="muted small-text">
                    {asMs(r.updated_at)
                      ? new Date(asMs(r.updated_at)!).toLocaleString('zh-CN', { hour12: false })
                      : '—'}
                  </span>
                </div>
              ))}
            </div>
          )}
          {!receipts.length && (
            <Empty title="暂无回执" text="设备执行下行动作后，终态回执会记录在这里。" />
          )}
          <p className="form-hint">过期未达终态的记录按「未知」呈现并标注判定来源；只读展示，不回写。</p>
        </div>
      )}

      {confirm === 'pause' && (
        <div className="confirm-strip" role="alert">
          <p>暂停后设备注册保留、连接不断开，但全部动作与查询将被门控拒绝（fail-closed）。可随时恢复。</p>
          <div className="form-actions">
            <button className="btn" disabled={busy} onClick={() => setConfirm(null)}>
              取消
            </button>
            <button
              className="btn danger"
              disabled={busy}
              onClick={() => {
                setConfirm(null);
                void onRun(device.device_id, 'pause', {}, '设备已暂停。');
              }}
            >
              确认暂停
            </button>
          </div>
        </div>
      )}
      {confirm === 'revoke' && (
        <div className="confirm-strip" role="alert">
          <p>吊销即时生效并踢除在线连接。吊销后不可恢复，只能彻底删除后重新走配对仪式。确定吊销？</p>
          <div className="form-actions">
            <button className="btn" disabled={busy} onClick={() => setConfirm(null)}>
              取消
            </button>
            <button
              className="btn danger"
              disabled={busy}
              onClick={() => {
                setConfirm(null);
                void onRevoke(device.device_id);
              }}
            >
              确认吊销
            </button>
          </div>
        </div>
      )}
      {confirm === 'purge' && (
        <div className="confirm-strip" role="alert">
          <p>彻底删除将清除设备注册记录，设备必须重新完成配对码审批才能再次接入。此操作无法撤销。</p>
          <div className="form-actions">
            <button className="btn" disabled={busy} onClick={() => setConfirm(null)}>
              取消
            </button>
            <button
              className="btn danger"
              disabled={busy}
              onClick={() => {
                setConfirm(null);
                void onRun(device.device_id, 'purge', {}, '设备注册已删除。');
              }}
            >
              确认彻底删除
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
