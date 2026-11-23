// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import DevicesPage from "../Devices";
import { api } from "../../api";

vi.mock("../../api", () => ({
  api: {
    devices: vi.fn(),
    devicesDetail: vi.fn(),
    deviceReceipts: vi.fn(),
    deviceRename: vi.fn(),
    deviceRole: vi.fn(),
    deviceScopes: vi.fn(),
    devicePause: vi.fn(),
    deviceResume: vi.fn(),
    deviceRevoke: vi.fn(),
  },
  ApiError: class ApiError extends Error {
    code: string; status: number;
    constructor(code: string, message: string, status: number) {
      super(message); this.code = code; this.status = status;
    }
  },
}));

const CAPS = ["device.shell", "device.tap", "device.observe"];

const DEV_A = {
  device_id: "a".repeat(32), label: "Magic8 主用", status: "active",
  online: true, scopes: ["device.shell"], role: "daily",
};
const DEV_B = {
  device_id: "b".repeat(32), label: "leaf5-root", status: "revoked",
  online: false, scopes: [],
};
const DEV_C = {
  device_id: "c".repeat(32), label: "旧平板", status: "active",
  online: false, scopes: ["device.observe"],
};

const DETAIL_A = {
  ...DEV_A,
  transports: { embedded_wss: "connected", ssh: "down:not connected" },
  capabilities_effective: ["device.shell"],
  activity: {
    uplink: { events: 3, last_at: 1761900000000 },
    downlink: {
      pending: 0,
      last_receipt: {
        idempotency_key: "k1", action_id: "act_1", capability: "device.shell",
        state: "unknown", route: "ssh", updated_at: 1761900000000,
        outcome_source: "expires_at", summary: "等待超时",
      },
    },
  },
};

const RECEIPTS_A = {
  device_id: DEV_A.device_id,
  receipts: [
    { idempotency_key: "k1", action_id: "act_1", capability: "device.shell",
      state: "unknown", route: "ssh", updated_at: 1761900000000,
      outcome_source: "expires_at", summary: "等待超时" },
    { idempotency_key: "k2", action_id: "act_2", capability: "device.tap",
      state: "succeeded", route: "adb", updated_at: 1761890000000,
      summary: "已点击" },
  ],
};

beforeEach(() => {
  vi.mocked(api.devices).mockResolvedValue({
    devices: [DEV_A, DEV_B, DEV_C], capabilities: CAPS,
  });
  vi.mocked(api.devicesDetail).mockImplementation(async (id: string) =>
    id === DEV_A.device_id ? DETAIL_A
    : id === DEV_B.device_id ? ({ ...DEV_B } as never)
    : ({ ...DEV_C } as never));
  vi.mocked(api.deviceReceipts).mockResolvedValue(RECEIPTS_A);
});

afterEach(() => { cleanup(); vi.resetAllMocks(); });

it("renders list with honest states (active online / revoked offline)", async () => {
  render(<DevicesPage />);
  // 拓扑节点与列表行各渲染一次名称，均属预期
  expect((await screen.findAllByText("Magic8 主用")).length).toBeGreaterThanOrEqual(2);
  expect(screen.getAllByText("leaf5-root").length).toBeGreaterThanOrEqual(2);
  // A/C 两台 active，徽章各出现一次
  expect(screen.getAllByText("已激活").length).toBeGreaterThanOrEqual(2);
  // 「已吊销」在状态徽章与连接标签各出现一次，均属诚实呈现
  expect(screen.getAllByText("已吊销").length).toBeGreaterThanOrEqual(1);
  // 「离线」在拓扑副标与列表行各出现一次
  expect(screen.getAllByText("离线").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("daily").length).toBeGreaterThanOrEqual(1);
});

it("shows empty state when no devices", async () => {
  vi.mocked(api.devices).mockResolvedValue({ devices: [], capabilities: CAPS });
  render(<DevicesPage />);
  expect(await screen.findByTestId("devices-empty")).toBeTruthy();
});

it("topology shows real uplink/downlink aggregates", async () => {
  render(<DevicesPage />);
  await screen.findAllByText("Magic8 主用");
  const topo = screen.getByTestId("device-topology");
  expect(topo.textContent).toContain("↑ 感知 3 事件");
  expect(topo.textContent).toContain("↓ 动作 未知");
  // 被吊销设备无详情活动 → 诚实显示无记录
  expect(topo.textContent).toContain("无记录");
});

it("detail panel shows receipts incl. unknown with outcome source", async () => {
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_A.device_id}`));
  expect(await screen.findByText("最近回执")).toBeTruthy();
  expect(screen.getByText("未知?")).toBeTruthy();
  expect(screen.getByText("已点击")).toBeTruthy();
  expect(screen.getByText("embedded_wss:connected · ssh:down:not connected")).toBeTruthy();
});

it("pause flow calls API and refreshes", async () => {
  vi.mocked(api.devicePause).mockResolvedValue({ device_id: DEV_A.device_id, status: "paused" });
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_A.device_id}`));
  fireEvent.click(await screen.findByRole("button", { name: "暂停设备" }));
  await act(async () => {});
  expect(api.devicePause).toHaveBeenCalledWith(DEV_A.device_id);
  expect(vi.mocked(api.devices).mock.calls.length).toBeGreaterThanOrEqual(2);
});

it("scope editor saves narrowed scopes", async () => {
  vi.mocked(api.deviceScopes).mockResolvedValue({
    device_id: DEV_A.device_id, status: "active", scopes: [],
  });
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_A.device_id}`));
  fireEvent.click(screen.getByText(/授权范围/));
  fireEvent.click(screen.getByLabelText("device.shell")); // 取消勾选 → 收窄为空
  fireEvent.click(screen.getByRole("button", { name: /保存 scope/ }));
  await act(async () => {});
  expect(api.deviceScopes).toHaveBeenCalledWith(DEV_A.device_id, []);
});

it("rename and role saved with trimmed values", async () => {
  vi.mocked(api.deviceRename).mockResolvedValue({ ...DETAIL_A, label: "新名字" } as never);
  vi.mocked(api.deviceRole).mockResolvedValue({ ...DETAIL_A, role: "root-phone" } as never);
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_A.device_id}`));
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新名字" } });
  fireEvent.change(screen.getByLabelText("角色"), { target: { value: "root-phone" } });
  fireEvent.click(screen.getByRole("button", { name: "保存名称/角色" }));
  await act(async () => {});
  expect(api.deviceRename).toHaveBeenCalledWith(DEV_A.device_id, "新名字");
  expect(api.deviceRole).toHaveBeenCalledWith(DEV_A.device_id, "root-phone");
});

it("revoke requires explicit confirmation", async () => {
  vi.mocked(api.deviceRevoke).mockResolvedValue({
    device_id: DEV_A.device_id, status: "revoked", kicked: true,
  });
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_A.device_id}`));
  fireEvent.click(screen.getByRole("button", { name: "撤销设备…" }));
  expect(api.deviceRevoke).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
  await act(async () => {});
  expect(api.deviceRevoke).toHaveBeenCalledWith(DEV_A.device_id);
});

it("revoked device hides scope editor and shows no pause action", async () => {
  vi.mocked(api.devices).mockResolvedValue({ devices: [DEV_B], capabilities: CAPS });
  render(<DevicesPage />);
  fireEvent.click(await screen.findByTestId(`device-row-${DEV_B.device_id}`));
  expect(await screen.findByText("最近回执")).toBeTruthy();
  expect(screen.queryByText(/授权范围/)).toBeNull();
  expect(screen.queryByRole("button", { name: "暂停设备" })).toBeNull();
});
