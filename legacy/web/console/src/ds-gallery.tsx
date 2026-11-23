/**
 * 设计系统自检画廊（仅 dev/e2e；不会进入部署构建——build-for-whale.mjs 只打 index.html）。
 * 内容是组件演示（静态样例），不代表任何服务数据。
 */
import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Moon, Sun, Zap } from 'lucide-react';
import {
  Banner,
  Button,
  Card,
  CheckRow,
  Field,
  FocusCardTitle,
  FocusGrid,
  IconButton,
  Input,
  SegmentedControl,
  Select,
  Switch,
  Tag,
  Textarea,
  type FocusGridItem,
} from './ds';
import './index.css';

function ThemeToggle() {
  const [dark, setDark] = useState(
    () => document.documentElement.dataset.theme === 'dark',
  );
  return (
    <IconButton
      label={dark ? '切换浅色' : '切换深色'}
      onClick={() => {
        const next = !dark;
        setDark(next);
        document.documentElement.dataset.theme = next ? 'dark' : 'light';
        try {
          localStorage.setItem('whale.theme', next ? 'dark' : 'light');
        } catch {
          /* 偏好仅存当前页 */
        }
      }}
    >
      {dark ? <Sun size={16} /> : <Moon size={16} />}
    </IconButton>
  );
}

function FocusDemo() {
  const [focused, setFocused] = useState<string | null>(null);
  const [draft, setDraft] = useState(''); // 验证：折叠/展开不丢输入
  const roles = [
    { key: 'fast', name: '回复模型（快）', desc: '日常对话、即时响应', provider: 'OpenAI', model: 'gpt-5.4-mini', effort: 'Low' },
    { key: 'slow', name: '回复模型（慢）', desc: '复杂推理、深度思考', provider: 'Anthropic', model: 'Claude Sonnet 4.5', effort: 'High' },
    { key: 'vision', name: '视觉模型', desc: '图像理解、识别与分析', provider: 'OpenAI', model: 'gpt-4o', effort: '' },
    { key: 'intent', name: '意图模型', desc: '意图识别、路由与任务规划', provider: 'DeepSeek', model: 'deepseek-v3', effort: 'Medium' },
  ];
  const items: FocusGridItem[] = roles.map((r) => ({
    key: r.key,
    ariaLabel: r.name,
    head: () => (
      <FocusCardTitle
        icon={<Zap size={19} />}
        name={r.name}
        desc={r.desc}
        aside={<Tag tone="mint" dot>启用</Tag>}
      />
    ),
    body: (isFocused) =>
      isFocused ? (
        <div className="demo-focus-body">
          <div className="demo-row">
            <Field label="供应商">
              <Select aria-label={`${r.name}供应商`} defaultValue={r.provider}>
                <option>{r.provider}</option>
                <option>另一个供应商</option>
              </Select>
            </Field>
            <Field label="模型">
              <Select aria-label={`${r.name}模型`} defaultValue={r.model}>
                <option>{r.model}</option>
              </Select>
            </Field>
            <Field label="思考强度">
              <SegmentedControl
                label="思考强度"
                value={r.effort || 'Off'}
                onChange={() => undefined}
                options={[
                  { value: 'Off', label: '关闭' },
                  { value: 'Low', label: 'Low' },
                  { value: 'Medium', label: 'Medium' },
                  { value: 'High', label: 'High' },
                ]}
              />
            </Field>
          </div>
          <Field label="高级设置草稿（验证输入保持）" hint="折叠再展开，这里的内容不应丢失。">
            <Input
              aria-label="高级设置草稿"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="输入点什么再折叠…"
            />
          </Field>
          <div className="demo-actions">
            <Button variant="ghost" size="sm" onClick={() => setFocused(null)}>
              收起
            </Button>
            <Button variant="secondary" size="sm">
              重置
            </Button>
            <Button variant="primary" size="sm">
              保存配置
            </Button>
          </div>
        </div>
      ) : (
        <div className="demo-compact">
          <span>{r.provider}</span>
          <span>{r.model}</span>
          {r.effort && <span>思考强度：{r.effort}</span>}
        </div>
      ),
  }));
  return (
    <Card
      title="FocusGrid · 平级卡片与动态聚焦"
      subtitle="点击卡片聚焦展开；点击其他卡片切换；再点聚焦卡或「收起」恢复平级。"
    >
      <FocusGrid items={items} focused={focused} onFocus={setFocused} />
      <p className="demo-note">
        当前聚焦：{focused ?? '（平级）'}；草稿内容：{draft || '（空）'}
      </p>
    </Card>
  );
}

function Gallery() {
  const [switchOn, setSwitchOn] = useState(true);
  const [checked, setChecked] = useState(true);
  const [seg, setSeg] = useState('Medium');
  return (
    <main className="demo-shell">
      <header className="demo-head">
        <h1>Whale Core 设计系统自检</h1>
        <p>组件演示（静态样例，非服务数据）· 仅开发与测试入口</p>
        <ThemeToggle />
      </header>

      <Card title="按钮 Button" subtitle="primary / secondary / danger / ghost × md/sm + busy">
        <div className="demo-row">
          <Button variant="primary">保存配置</Button>
          <Button variant="secondary">重置</Button>
          <Button variant="danger">吊销设备</Button>
          <Button variant="ghost">收起</Button>
          <Button variant="primary" busy>
            保存中
          </Button>
          <Button size="sm" variant="secondary">
            小按钮
          </Button>
        </div>
      </Card>

      <Card title="Tag · 状态标签" subtitle="六种语义色 + 状态点">
        <div className="demo-row">
          <Tag tone="violet">规划中</Tag>
          <Tag tone="mint" dot>在线</Tag>
          <Tag tone="blue">执行中</Tag>
          <Tag tone="amber" dot>已暂停</Tag>
          <Tag tone="red">失败</Tag>
          <Tag tone="slate">已吊销</Tag>
        </div>
      </Card>

      <Card title="表单 Field / Input / Select / Switch / SegmentedControl / CheckRow">
        <div className="demo-row">
          <Field label="模型名称" hint="OpenAI 兼容模型 ID">
            <Input aria-label="模型名称" placeholder="deepseek-chat" />
          </Field>
          <Field label="供应商">
            <Select aria-label="供应商">
              <option>DeepSeek</option>
              <option>OpenAI</option>
            </Select>
          </Field>
          <Field label="思考强度">
            <SegmentedControl
              label="思考强度"
              value={seg}
              onChange={setSeg}
              options={[
                { value: 'Off', label: '关闭' },
                { value: 'Low', label: 'Low' },
                { value: 'Medium', label: 'Medium' },
                { value: 'High', label: 'High' },
              ]}
            />
          </Field>
        </div>
        <div className="demo-row">
          <Field inline label="启用流式输出">
            <Switch label="启用流式输出" checked={switchOn} onChange={setSwitchOn} />
          </Field>
          <CheckRow label="保存时清除此密钥" checked={checked} onChange={setChecked} />
        </div>
        <Field label="人格禁忌" error="每行一条，不能为空行">
          <Textarea aria-label="人格禁忌" defaultValue={'不谈论政治'} />
        </Field>
      </Card>

      <Card title="Banner · 提示" subtitle="info / ok / warn / error">
        <div className="demo-col">
          <Banner tone="info">会话 cookie 为 HttpOnly + SameSite=Strict。</Banner>
          <Banner tone="ok">设置已保存并生效。</Banner>
          <Banner tone="warn">切换运行方式后，旧密钥不会沿用。</Banner>
          <Banner tone="error">模型连接失败：无有效回复。</Banner>
        </div>
      </Card>

      <FocusDemo />
    </main>
  );
}

// 挂载前应用持久化主题
try {
  const saved = localStorage.getItem('whale.theme');
  if (saved) document.documentElement.dataset.theme = saved;
} catch {
  /* 忽略 */
}
createRoot(document.getElementById('root')!).render(<Gallery />);
