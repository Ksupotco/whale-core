/**
 * #60 设备管理页：列表 / 详情 / 拓扑（数据全部来自真实连接与 kv 任务状态）。
 *
 * 纪律：
 * - 无静态假数据：列表=GET /devices?transports=1，详情=GET /devices/{id}，
 *   回执=GET /devices/{id}/receipts，拓扑=各设备 activity 聚合；
 * - 空/断线/暂停/吊销/未知回执都有诚实展示；
 * - 写操作（重命名/角色/scope/暂停/恢复/撤销）以后端裁决为准，成功后刷新。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  type DeviceActivity,
  type DeviceDetail,
  type DeviceListItem,
  type DeviceReceipt,
} from "../api";

type Snapshot<T> = { data: T | null; error: string | null; at: number | null };
const empty = <T,>(): Snapshot<T> => ({ data: null, error: null, at: null });

const STATE_LABEL: Record<string, string> = {
  succeeded: "成功",
  failed: "失败",
  timeout: "超时",
  cancelled: "已取消",
  unknown: "未知",
  received: "已接收",
  pending: "在飞",
};

const STATUS_LABEL: Record<string, string> = {
  active: "已激活",
  pending: "待配对",
  paused: "已暂停",
  revoked: "已吊销",
};

function timeLabel(value?: number | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

function StateBadge({ state, outcome }: { state: string; outcome?: string }) {
  const cls = `device-state device-state-${state}`;
  return (
    <span className={cls} title={outcome ? `来源：${outcome}` : undefined}>
      {STATE_LABEL[state] ?? state}
      {state === "unknown" && outcome ? "?" : ""}
    </span>
  );
}

function onlineLabel(d: DeviceListItem): string {
  if (d.status === "revoked") return "已吊销";
  if (d.status === "paused") return "已暂停";
  if (d.status === "pending") return "待配对";
  if (d.online === true) return "在线";
  if (d.online === false) return "离线";
  return "连接未知";
}

// ---------- 拓扑（上行=感知事件，下行=动作回执） ----------

function Topology({
  devices,
  details,
  onSelect,
  selected,
}: {
  devices: DeviceListItem[];
  details: Map<string, DeviceDetail>;
  onSelect: (id: string) => void;
  selected: string | null;
}) {
  const W = 760;
  const ROW = 74;
  const H = Math.max(120, 40 + devices.length * ROW);
  const coreX = 90;
  const devX = 560;
  const coreY = H / 2;
  return (
    <svg
      className="device-topology"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label="设备拓扑（上行感知/下行动作）"
      data-testid="device-topology"
    >
      <rect x={coreX - 46} y={coreY - 24} width={92} height={48} rx={10} className="topo-core" />
      <text x={coreX} y={coreY + 4} textAnchor="middle" className="topo-core-label">Core</text>
      {devices.map((d, i) => {
        const y = 40 + i * ROW + 22;
        const act: DeviceActivity | undefined = details.get(d.device_id)?.activity;
        const up = act ? `${act.uplink.events} 事件` : "无记录";
        const down = act
          ? act.downlink.last_receipt
            ? `${STATE_LABEL[act.downlink.last_receipt.state] ?? act.downlink.last_receipt.state}${act.downlink.pending ? ` · ${act.downlink.pending} 在飞` : ""}`
            : "无动作"
          : "无记录";
        const dead = d.status === "revoked" || d.online === false;
        return (
          <g key={d.device_id}>
            <path
              d={`M ${coreX + 46} ${coreY - 8} C ${coreX + 200} ${coreY - 8}, ${devX - 200} ${y - 8}, ${devX - 8} ${y - 8}`}
              className={`topo-edge topo-up${dead ? " dead" : ""}`}
              markerEnd="url(#arrow-up)"
            />
            <path
              d={`M ${devX - 8} ${y + 8} C ${devX - 200} ${y + 8}, ${coreX + 200} ${coreY + 8}, ${coreX + 46} ${coreY + 8}`}
              className={`topo-edge topo-down${dead ? " dead" : ""}`}
              markerEnd="url(#arrow-down)"
            />
            <text x={(coreX + devX) / 2} y={y - 16} textAnchor="middle" className="topo-edge-label">↑ 感知 {up}</text>
            <text x={(coreX + devX) / 2} y={y + 26} textAnchor="middle" className="topo-edge-label">↓ 动作 {down}</text>
            <g
              onClick={() => onSelect(d.device_id)}
              style={{ cursor: "pointer" }}
              data-testid={`topo-node-${d.device_id}`}
            >
              <rect
                x={devX} y={y - 22} width={170} height={48} rx={10}
                className={`topo-device${selected === d.device_id ? " selected" : ""}${dead ? " dead" : ""}`}
              />
              <text x={devX + 85} y={y - 2} textAnchor="middle" className="topo-device-label">
                {d.label || d.device_id.slice(0, 8)}
              </text>
              <text x={devX + 85} y={y + 15} textAnchor="middle" className="topo-device-sub">
                {onlineLabel(d)}{d.role ? ` · ${d.role}` : ""}
              </text>
            </g>
          </g>
        );
      })}
      <defs>
        <marker id="arrow-up" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" className="topo-arrow-up" />
        </marker>
        <marker id="arrow-down" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" className="topo-arrow-down" />
        </marker>
      </defs>
    </svg>
  );
}

// ---------- 详情抽屉 ----------

function DeviceDetailPanel({
  device,
  allCaps,
  onChanged,
  onClose,
}: {
  device: DeviceDetail;
  allCaps: string[];
  onChanged: () => void;
  onClose: () => void;
}) {
  const [label, setLabel] = useState(device.label ?? "");
  const [role, setRole] = useState(device.role ?? "");
  const [scopes, setScopes] = useState<Set<string>>(new Set(device.scopes ?? []));
  const [receipts, setReceipts] = useState<Snapshot<DeviceReceipt[]>>(empty);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmRevoke, setConfirmRevoke] = useState(false);

  const loadReceipts = useCallback(async () => {
    try {
      const res = await api.deviceReceipts(device.device_id, 20);
      setReceipts({ data: res.receipts, error: null, at: Date.now() });
    } catch (err) {
      setReceipts((old) => ({
        ...old,
        error: err instanceof Error ? err.message : "回执读取失败",
      }));
    }
  }, [device.device_id]);

  useEffect(() => {
    setLabel(device.label ?? "");
    setRole(device.role ?? "");
    setScopes(new Set(device.scopes ?? []));
    setConfirmRevoke(false);
    setError(null);
    void loadReceipts();
  }, [device, loadReceipts]);

  const act = async (fn: () => Promise<unknown>, refresh = true) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (refresh) onChanged();
      await loadReceipts();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.message}（${err.code}）` : "操作失败");
    } finally {
      setBusy(false);
    }
  };

  const dirty =
    label !== (device.label ?? "") || role !== (device.role ?? "");
  const scopesDirty = (() => {
    const old = new Set(device.scopes ?? []);
    if (old.size !== scopes.size) return true;
    for (const s of scopes) if (!old.has(s)) return true;
    return false;
  })();

  const paused = device.status === "paused";
  const revoked = device.status === "revoked";

  return (
    <aside className="device-detail" data-testid="device-detail">
      <div className="section-head">
        <h3>{device.label || device.device_id}</h3>
        <button className="text-button" onClick={onClose}>收起</button>
      </div>
      {error && <p className="banner error" role="alert">{error}</p>}
      <dl className="device-meta">
        <dt>设备 ID</dt><dd className="mono">{device.device_id}</dd>
        <dt>状态</dt>
        <dd>{STATUS_LABEL[device.status] ?? device.status} · {onlineLabel(device)}</dd>
        {device.transports && (
          <>
            <dt>通道</dt>
            <dd className="mono">
              {Object.entries(device.transports)
                .map(([k, v]) => `${k}:${v}`)
                .join(" · ")}
            </dd>
          </>
        )}
      </dl>

      <div className="device-edit">
        <label>
          名称
          <input value={label} maxLength={64} disabled={busy}
                 onChange={(e) => setLabel(e.target.value)} />
        </label>
        <label>
          角色
          <input value={role} maxLength={32} placeholder="如 root-phone / daily / glasses"
                 disabled={busy} onChange={(e) => setRole(e.target.value)} />
        </label>
        <button disabled={busy || !dirty}
                onClick={() => act(async () => {
                  if (label !== (device.label ?? "")) await api.deviceRename(device.device_id, label.trim());
                  if (role !== (device.role ?? "")) await api.deviceRole(device.device_id, role.trim());
                })}>保存名称/角色</button>
      </div>

      {!revoked && (
        <details className="device-scopes">
          <summary>授权范围（{scopes.size}/{allCaps.length}）{paused || device.status !== "active" ? " · 仅 active 可改" : ""}</summary>
          <div className="device-scopes-grid">
            {allCaps.map((cap) => (
              <label key={cap} className="mono">
                <input
                  type="checkbox"
                  checked={scopes.has(cap)}
                  disabled={busy || device.status !== "active"}
                  onChange={(e) => {
                    const next = new Set(scopes);
                    if (e.target.checked) next.add(cap);
                    else next.delete(cap);
                    setScopes(next);
                  }}
                />
                {cap}
              </label>
            ))}
          </div>
          <button disabled={busy || !scopesDirty || device.status !== "active"}
                  onClick={() => act(() => api.deviceScopes(device.device_id, [...scopes]))}>
            保存 scope（收窄即停自主体）
          </button>
        </details>
      )}

      <div className="device-actions">
        {device.status === "active" && (
          <button disabled={busy} onClick={() => act(() => api.devicePause(device.device_id))}>
            暂停设备
          </button>
        )}
        {paused && (
          <button disabled={busy} onClick={() => act(() => api.deviceResume(device.device_id))}>
            恢复设备
          </button>
        )}
        {!revoked && !confirmRevoke && (
          <button className="studio-danger" disabled={busy} onClick={() => setConfirmRevoke(true)}>
            撤销设备…
          </button>
        )}
        {confirmRevoke && (
          <span>
            <strong>确认撤销？即时生效并踢下线，须重新配对。</strong>
            <button className="studio-danger" disabled={busy}
                    onClick={() => act(() => api.deviceRevoke(device.device_id))}>
              确认撤销
            </button>
            <button className="text-button" disabled={busy} onClick={() => setConfirmRevoke(false)}>
              取消
            </button>
          </span>
        )}
      </div>

      <h4>最近回执</h4>
      {receipts.error && <p className="banner error" role="alert">{receipts.error}</p>}
      {!receipts.data?.length && <p className="muted">暂无动作回执。</p>}
      {!!receipts.data?.length && (
        <table className="device-receipts">
          <thead>
            <tr><th>状态</th><th>能力</th><th>通道</th><th>时间</th><th>摘要</th></tr>
          </thead>
          <tbody>
            {receipts.data.map((r) => (
              <tr key={r.action_id || r.idempotency_key}>
                <td><StateBadge state={r.state} outcome={r.outcome_source} /></td>
                <td className="mono">{r.capability}</td>
                <td className="mono">{r.route}</td>
                <td>{timeLabel(r.updated_at)}</td>
                <td className="device-receipt-summary" title={r.summary}>{r.summary || r.error_code || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </aside>
  );
}

// ---------- 页面 ----------

type ListState = Snapshot<DeviceListItem[]> & { caps?: string[] };

export default function DevicesPage() {
  const [list, setList] = useState<ListState>({ data: null, error: null, at: null });
  const [details, setDetails] = useState<Map<string, DeviceDetail>>(new Map());
  const [selected, setSelected] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const res = await api.devices();
      // 逐设备取详情（真实连接 + 任务活动）；失败保留旧数据
      const entries = await Promise.all(
        res.devices.map(async (d) => {
          try {
            return [d.device_id, await api.devicesDetail(d.device_id)] as const;
          } catch {
            return null;
          }
        }),
      );
      setList({ data: res.devices, error: null, at: Date.now(), caps: res.capabilities });
      setDetails((old) => {
        const next = new Map(old);
        for (const e of entries) if (e) next.set(e[0], e[1] as DeviceDetail);
        return next;
      });
    } catch (err) {
      setList((old) => ({
        ...old,
        error: err instanceof Error ? err.message : "设备列表读取失败",
      }));
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh, tick]);

  const devices = useMemo(() => list.data ?? [], [list.data]);
  const selectedDetail = selected ? details.get(selected) ?? null : null;

  return (
    <section className="card devices-page" aria-label="设备管理">
      <div className="section-head">
        <h2>设备管理</h2>
        <button className="text-button" onClick={() => setTick((n) => n + 1)}>刷新</button>
      </div>
      {list.error && <p className="banner error" role="alert">{list.error}</p>}
      {list.data && devices.length === 0 && (
        <p className="muted" data-testid="devices-empty">
          尚未登记设备。请先在手机端发起配对，或用配对 API 登记。
        </p>
      )}
      {devices.length > 0 && (
        <>
          <Topology devices={devices} details={details} selected={selected}
                    onSelect={(id) => setSelected(id === selected ? null : id)} />
          <ul className="device-list">
            {devices.map((d) => (
              <li key={d.device_id}>
                <button
                  className={`device-row${selected === d.device_id ? " selected" : ""}`}
                  onClick={() => setSelected(selected === d.device_id ? null : d.device_id)}
                  data-testid={`device-row-${d.device_id}`}
                >
                  <strong>{d.label || "未命名设备"}</strong>
                  {d.role && <span className="device-role">{d.role}</span>}
                  <span className={`device-state device-state-${d.status}`}>
                    {STATUS_LABEL[d.status] ?? d.status}
                  </span>
                  <span className="muted">{onlineLabel(d)}</span>
                  <span className="muted">{d.scopes?.length ?? 0} 项授权</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {selectedDetail && (
        <DeviceDetailPanel
          device={selectedDetail}
          allCaps={list.caps ?? []}
          onChanged={() => void refresh()}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  );
}
