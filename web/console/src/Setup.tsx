import { useState, type FormEvent } from 'react';
import { Eye, EyeOff, Link2, LoaderCircle, ShieldCheck, Sparkles } from 'lucide-react';
import { WhaleLogo } from './ui';
import { useWorkspace } from './workspace';

// 首开配置流：认证（管理凭据换会话）→ 偏好。完成后永远连接真实服务，
// 无本地示例模式。令牌只存在于本表单的一次提交中，不写入任何存储。
export default function SetupPage() {
  const { authenticate, enterConsole, status, sessionExpiresAt } = useWorkspace();
  const [step, setStep] = useState<'auth' | 'prefs'>('auth');
  const [token, setToken] = useState('');
  const [reveal, setReveal] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [motion, setMotion] = useState(document.documentElement.dataset.motion !== 'off');

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!token.trim()) return;
    setBusy(true);
    setError('');
    try {
      await authenticate(token.trim());
      setToken(''); // 即用即弃
      setStep('prefs');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const saveMotion = (on: boolean) => {
    setMotion(on);
    document.documentElement.dataset.motion = on ? 'on' : 'off';
    try {
      localStorage.setItem('whale.motion', on ? 'on' : 'off');
    } catch {
      /* 偏好仅当前标签页生效 */
    }
  };

  return (
    <div className="setup-shell">
      <div className="setup-card">
        <div className="setup-brand">
          <WhaleLogo size={52} />
          <div>
            <strong>Whale Core</strong>
            <small>INTELLIGENCE CONSOLE</small>
          </div>
        </div>
        <div className="setup-steps" aria-label="配置进度">
          <span className={step === 'auth' ? 'active' : 'done'}>1 认证</span>
          <i />
          <span className={step === 'prefs' ? 'active' : ''}>2 偏好</span>
        </div>

        {step === 'auth' && (
          <form className="form-stack" onSubmit={submit}>
            <div>
              <h1>连接你的智能中枢</h1>
              <p className="setup-lead">
                此控制台由 Whale Core 同源伺服。输入服务配置目录 <code>api_token</code>{' '}
                中的管理令牌完成认证——它将换取一个有时效的会话，此后打开页面即达真实数据。
              </p>
            </div>
            <label>
              管理令牌
              <div className="password-field">
                <input
                  required
                  autoFocus
                  type={reveal ? 'text' : 'password'}
                  value={token}
                  autoComplete="off"
                  onChange={(e) => setToken(e.target.value)}
                  placeholder="输入管理令牌"
                  aria-label="管理令牌"
                />
                <button
                  type="button"
                  aria-label={reveal ? '隐藏令牌' : '显示令牌'}
                  onClick={() => setReveal(!reveal)}
                >
                  {reveal ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
              <small>令牌仅用于本次会话交换，不会被保存；刷新页面后由浏览器会话 cookie 接管。</small>
            </label>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            <button className="btn primary setup-submit" disabled={busy || !token.trim()}>
              {busy ? <LoaderCircle className="spin" size={15} /> : <Link2 size={15} />}
              {busy ? '正在验证...' : '认证并连接'}
            </button>
            <div className="settings-security">
              <ShieldCheck size={15} />
              会话经 HttpOnly cookie 维持（SameSite=Strict、12 小时有效），不写入 localStorage。
            </div>
          </form>
        )}

        {step === 'prefs' && (
          <div className="form-stack">
            <div>
              <h1>一切就绪</h1>
              <p className="setup-lead">
                已连接 Whale Core 服务{status?.profile ? `（${status.profile}）` : ''}
                {sessionExpiresAt
                  ? `，会话有效至 ${new Date(sessionExpiresAt).toLocaleString('zh-CN', { hour12: false })}`
                  : ''}
                。
              </p>
            </div>
            <div className="setting-row">
              <div>
                <strong>界面动效</strong>
                <small>页面过渡与趋势线绘制动画，之后可在设置中更改。</small>
              </div>
              <button
                className={`switch ${motion ? 'on' : ''}`}
                role="switch"
                aria-label="界面动效"
                aria-checked={motion}
                onClick={() => saveMotion(!motion)}
              >
                <span />
              </button>
            </div>
            <button
              className="btn primary setup-submit"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await enterConsole();
                } catch (err) {
                  setError((err as Error).message);
                  setStep('auth');
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}
              进入控制台
            </button>
          </div>
        )}
      </div>
      <p className="setup-foot">Whale Core Console · 同源部署 · 不内置任何凭据</p>
    </div>
  );
}
