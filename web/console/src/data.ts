export type Page =
  | 'overview'
  | 'chat'
  | 'memory'
  | 'personas'
  | 'models'
  | 'providers'
  | 'tasks'
  | 'plugins'
  | 'workflows'
  | 'devices'
  | 'approvals'
  | 'events'
  | 'logs'
  | 'settings';
export type Kind = 'chat' | 'memory' | 'tasks' | 'plugins' | 'workflows' | 'system';
export type MemoryItem = { id: string; title: string; body: string; category: string; at: number };
export type Task = { id: string; title: string; description: string; status: string; at: number };
export type Plugin = {
  id: string;
  name: string;
  english: string;
  description: string;
  enabled: boolean;
  version: string;
  kind: string;
  inputs?: string[];
  actions?: string[];
  provides?: string[];
};
export type Activity = { id: string; kind: Kind; title: string; description: string; at: number };
export type Message = { id: string; role: 'user' | 'assistant'; text: string };
export type Thread = { id: string; title: string; messages: Message[] };
export type Day = { date: string; messages: number; memories: number };
export type Workspace = {
  memories: MemoryItem[];
  tasks: Task[];
  plugins: Plugin[];
  events: Activity[];
  threads: Thread[];
  days: Day[];
  name: string;
  version: string;
};
export const pages: Record<Page, { name: string; en: string; description: string; short: string }> = {
  overview: {
    name: '控制台',
    en: 'Overview',
    description: '欢迎回来，这是你的智能中枢运行概览。',
    short: '总览',
  },
  chat: {
    name: '对话',
    en: 'Conversations',
    description: '从一个问题开始，让思考自然延伸。',
    short: '对话',
  },
  memory: {
    name: '记忆',
    en: 'Memory',
    description: '收藏有价值的片段，让每一次对话都有迹可循。',
    short: '记忆',
  },
  models: {
    name: '模型配置',
    en: 'Models',
    description: '为不同任务配置合适的模型，支持多供应商与参数化设置。',
    short: '模型',
  },
  providers: {
    name: '供应商管理',
    en: 'Providers',
    description: '管理 OpenAI 兼容供应商的端点与凭据，供模型角色选用。',
    short: '供应商',
  },
  personas: {
    name: '角色与人格',
    en: 'Personas',
    description: '人物卡的浏览、编辑、导入与试玩对照。',
    short: '角色',
  },
  tasks: {
    name: '任务中心',
    en: 'Tasks',
    description: '管理待办事项，追踪每个任务的进展。',
    short: '任务',
  },
  plugins: {
    name: '插件与能力',
    en: 'Plugins',
    description: '按需启用能力，构建适合你的智能工作空间。',
    short: '插件',
  },
  workflows: {
    name: '探索工作流',
    en: 'Workflows',
    description: '把常用的工作步骤，变成可重复使用的流程。',
    short: '工作流',
  },
  devices: {
    name: '设备与连接',
    en: 'Devices',
    description: '管理已配对的设备与边缘端点，查看拓扑、回执与授权范围。',
    short: '设备',
  },
  approvals: {
    name: '审批队列',
    en: 'Approvals',
    description: '高风险任务与主动外发草稿，在这里等待你的确认。',
    short: '审批',
  },
  events: {
    name: '事件追踪',
    en: 'Events',
    description: 'Trace 账本、Span 瀑布与实时事件流，看清每一次运行。',
    short: '事件',
  },
  logs: {
    name: '运行日志',
    en: 'Activity',
    description: '查看工作区内的操作记录与运行动态。',
    short: '日志',
  },
  settings: {
    name: '系统设置',
    en: 'Settings',
    description: '管理工作区偏好，连接你的 Whale Core 服务。',
    short: '设置',
  },
};
export const kindNames: Record<Kind, string> = {
  chat: '对话',
  memory: '记忆',
  tasks: '任务',
  plugins: '插件',
  workflows: '工作流',
  system: '系统',
};
export const uid = () =>
  crypto.randomUUID?.() ||
  `w-${Date.now().toString(36)}-${crypto.getRandomValues(new Uint32Array(2)).join('')}`;
export const DAY = 86400000;
export function dayKey(time = Date.now()) {
  const d = new Date(time);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
export function shortDate(at: number) {
  return new Date(at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}
export function relativeTime(at: number) {
  const minutes = Math.max(0, Math.floor((Date.now() - at) / 60000));
  return minutes < 1
    ? '刚刚'
    : minutes < 60
      ? `${minutes} 分钟前`
      : minutes < 1440
        ? `${Math.floor(minutes / 60)} 小时前`
        : `${Math.floor(minutes / 1440)} 天前`;
}
export function makeEvent(kind: Kind, title: string, description: string): Activity {
  return { id: uid(), kind, title, description, at: Date.now() };
}
export function download(name: string, content: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
export function taskLabel(status: string) {
  return (
    (
      {
        pending: '待处理',
        pending_approval: '待审批',
        approved: '已批准',
        completed: '已完成',
        answered: '已完成',
        succeeded: '已完成',
        cancelled: '已取消',
        canceled: '已取消',
        denied: '已拒绝',
        failed: '失败',
        running: '执行中',
        clarify: '待补充',
        queued: '排队中',
      } as Record<string, string>
    )[status] || status
  );
}
