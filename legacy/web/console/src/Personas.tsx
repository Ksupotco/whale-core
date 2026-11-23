import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FlaskConical,
  LoaderCircle,
  Plus,
  ScanEye,
  Upload,
  UserRound,
} from 'lucide-react';
import {
  createPersona,
  fetchPersonas,
  personaEffective,
  personaImportPreview,
  personaPlaytest,
  updatePersona,
  type Persona,
  type PersonaCardInput,
  type PersonaDetail,
  type PersonaEffective,
  type PersonaImportPreview,
  type PersonaPlaytest,
} from './api';
import type { Connection } from './api';
import {
  Banner,
  Button,
  Field,
  FocusCardTitle,
  FocusGrid,
  Input,
  Tag,
  Textarea,
  type FocusGridItem,
} from './ds';
import { Empty, Modal, PageHeading } from './ui';
import { useWorkspace } from './workspace';

type FormState = {
  title: string;
  name: string;
  summary: string;
  category: string;
  author: string;
  source_url: string;
  license: string;
  artwork_ref: string;
  description: string;
  temperament: string;
  relationship_style: string;
  tone: string;
  address_user_as: string;
  style_rules: string;
  extra_taboos: string;
  external_prompt: string;
  example_dialogue: string;
  voice_hint: string;
  timbre_hint: string;
  speaking_rate: string;
};
const EMPTY: FormState = {
  title: '', name: '', summary: '', category: '', author: '', source_url: '', license: '',
  artwork_ref: '', description: '', temperament: '', relationship_style: '', tone: '',
  address_user_as: '', style_rules: '', extra_taboos: '', external_prompt: '',
  example_dialogue: '', voice_hint: '', timbre_hint: '', speaking_rate: '',
};
function toForm(p: Persona | PersonaDetail): FormState {
  return {
    title: p.title ?? '', name: p.name ?? '', summary: p.summary ?? '', category: p.category ?? '',
    author: p.author ?? '', source_url: p.source_url ?? '', license: p.license ?? '',
    artwork_ref: (p as PersonaDetail).artwork_ref ?? '',
    description: (p as PersonaDetail).description ?? '', temperament: p.temperament ?? '',
    relationship_style: p.relationship_style ?? '', tone: (p as PersonaDetail).tone ?? '',
    address_user_as: (p as PersonaDetail).address_user_as ?? '',
    style_rules: ((p as PersonaDetail).style_rules ?? []).join('\n'),
    extra_taboos: ((p as PersonaDetail).extra_taboos ?? []).join('\n'),
    external_prompt: (p as PersonaDetail).external_prompt ?? '',
    example_dialogue: (p as PersonaDetail).example_dialogue ?? '',
    voice_hint: p.voice_hint ?? '', timbre_hint: (p as PersonaDetail).timbre_hint ?? '',
    speaking_rate: (p as PersonaDetail).speaking_rate ? String((p as PersonaDetail).speaking_rate) : '',
  };
}
function toInput(f: FormState): PersonaCardInput {
  const lines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean);
  const rate = f.speaking_rate.trim() ? Number(f.speaking_rate) : undefined;
  return {
    title: f.title.trim(), name: f.name.trim(), summary: f.summary, category: f.category,
    author: f.author, source_url: f.source_url, license: f.license, artwork_ref: f.artwork_ref,
    description: f.description, temperament: f.temperament,
    relationship_style: f.relationship_style, tone: f.tone, address_user_as: f.address_user_as,
    style_rules: lines(f.style_rules), extra_taboos: lines(f.extra_taboos),
    external_prompt: f.external_prompt, example_dialogue: f.example_dialogue,
    voice_hint: f.voice_hint, timbre_hint: f.timbre_hint,
    ...(rate !== undefined && Number.isFinite(rate) ? { speaking_rate: rate } : {}),
  };
}

/** 单个角色卡（组件实例在聚焦/平级间常驻——FocusGrid 不重挂载，输入不丢）。 */
function PersonaCardView({
  persona,
  isNew,
  focused,
  connection,
  onSaved,
  onCancelNew,
  onPlaytest,
  onEffective,
}: {
  persona: PersonaDetail | null;
  isNew: boolean;
  focused: boolean;
  connection: Connection;
  onSaved: (p: PersonaDetail, created: boolean) => void;
  onCancelNew: () => void;
  onPlaytest: (id: string, form: FormState) => void;
  onEffective: (id: string) => void;
  notify: (text: string, error?: boolean) => void;
}) {
  const draftKey = `whale.persona-draft.${persona?.id ?? '__new__'}`;
  const [form, setForm] = useState<FormState>(() => {
    try {
      const saved = localStorage.getItem(draftKey);
      if (saved) return { ...EMPTY, ...JSON.parse(saved) };
    } catch {
      /* 草稿读取失败用空表 */
    }
    return persona ? toForm(persona) : EMPTY;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // 草稿自动保存（防抖 400ms）
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        localStorage.setItem(draftKey, JSON.stringify(form));
      } catch {
        /* 存储满则忽略 */
      }
    }, 400);
    return () => clearTimeout(t);
  }, [form, draftKey]);
  const set = (k: keyof FormState) => (v: string) => setForm((f) => ({ ...f, [k]: v }));
  const dirty = useMemo(
    () => JSON.stringify(toInput(form)) !== JSON.stringify(toInput(persona ? toForm(persona) : EMPTY)),
    [form, persona],
  );
  const reset = () => {
    setForm(persona ? toForm(persona) : EMPTY);
    setError('');
  };
  const save = async () => {
    if (!form.title.trim()) {
      setError('标题为必填项。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const input = toInput(form);
      const saved = isNew
        ? await createPersona(connection, input)
        : await updatePersona(connection, persona!.id, input);
      try {
        localStorage.removeItem(draftKey);
      } catch {
        /* 忽略 */
      }
      onSaved(saved, isNew);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const compact = (
    <div className="model-compact">
      <span className="model-compact-row">
        <em>性格</em>
        {form.temperament || '—'}
      </span>
      <span className="model-compact-row">
        <em>相处</em>
        {form.relationship_style || '—'}
      </span>
    </div>
  );
  if (!focused) return compact;
  return (
    <div className="model-form">
      <div className="model-form-grid">
        <Field label="标题" hint="必填。">
          <Input aria-label="标题" value={form.title} onChange={(e) => set('title')(e.target.value)} />
        </Field>
        <Field label="角色名">
          <Input aria-label="角色名" value={form.name} onChange={(e) => set('name')(e.target.value)} />
        </Field>
        <Field label="简短介绍">
          <Input aria-label="简短介绍" value={form.summary} onChange={(e) => set('summary')(e.target.value)} />
        </Field>
        <Field label="分类">
          <Input aria-label="分类" value={form.category} onChange={(e) => set('category')(e.target.value)} />
        </Field>
      </div>
      <details className="model-advanced">
        <summary>人格设定</summary>
        <div className="model-advanced-body">
          <Field label="描述">
            <Textarea aria-label="描述" rows={3} value={form.description} onChange={(e) => set('description')(e.target.value)} />
          </Field>
          <div className="model-form-grid">
            <Field label="性格特点">
              <Input aria-label="性格特点" value={form.temperament} onChange={(e) => set('temperament')(e.target.value)} />
            </Field>
            <Field label="相处方式">
              <Input aria-label="相处方式" value={form.relationship_style} onChange={(e) => set('relationship_style')(e.target.value)} />
            </Field>
            <Field label="语气">
              <Input aria-label="语气" value={form.tone} onChange={(e) => set('tone')(e.target.value)} />
            </Field>
            <Field label="称呼用户为">
              <Input aria-label="称呼用户为" value={form.address_user_as} onChange={(e) => set('address_user_as')(e.target.value)} />
            </Field>
          </div>
          <div className="model-form-grid">
            <Field label="风格规则（每行一条）">
              <Textarea aria-label="风格规则" rows={3} value={form.style_rules} onChange={(e) => set('style_rules')(e.target.value)} />
            </Field>
            <Field label="禁忌（每行一条）">
              <Textarea aria-label="禁忌" rows={3} value={form.extra_taboos} onChange={(e) => set('extra_taboos')(e.target.value)} />
            </Field>
          </div>
        </div>
      </details>
      <details className="model-advanced">
        <summary>提示词与示例</summary>
        <div className="model-advanced-body">
          <Field label="人设原文" hint="external_prompt：作为外部上下文注入，非指令。">
            <Textarea aria-label="人设原文" rows={4} value={form.external_prompt} onChange={(e) => set('external_prompt')(e.target.value)} />
          </Field>
          <Field label="示例对话" hint="进模型上下文的示例对话。">
            <Textarea aria-label="示例对话" rows={4} value={form.example_dialogue} onChange={(e) => set('example_dialogue')(e.target.value)} />
          </Field>
        </div>
      </details>
      <details className="model-advanced">
        <summary>语音与出处</summary>
        <div className="model-advanced-body">
          <div className="model-form-grid">
            <Field label="音色提示">
              <Input aria-label="音色提示" value={form.timbre_hint} onChange={(e) => set('timbre_hint')(e.target.value)} />
            </Field>
            <Field label="语音风格提示">
              <Input aria-label="语音风格提示" value={form.voice_hint} onChange={(e) => set('voice_hint')(e.target.value)} />
            </Field>
            <Field label="语速（0.5–2.0）">
              <Input aria-label="语速" type="number" step="0.05" min="0.5" max="2" value={form.speaking_rate} onChange={(e) => set('speaking_rate')(e.target.value)} />
            </Field>
          </div>
          <div className="model-form-grid">
            <Field label="作者">
              <Input aria-label="作者" value={form.author} onChange={(e) => set('author')(e.target.value)} />
            </Field>
            <Field label="许可证">
              <Input aria-label="许可证" value={form.license} onChange={(e) => set('license')(e.target.value)} />
            </Field>
            <Field label="来源链接">
              <Input aria-label="来源链接" type="url" value={form.source_url} onChange={(e) => set('source_url')(e.target.value)} />
            </Field>
            <Field label="形象引用">
              <Input aria-label="形象引用" value={form.artwork_ref} onChange={(e) => set('artwork_ref')(e.target.value)} />
            </Field>
          </div>
        </div>
      </details>
      {error && <Banner tone="error">{error}</Banner>}
      <div className="model-form-actions">
        {!isNew && (
          <>
            <Button variant="secondary" size="sm" onClick={() => onEffective(persona!.id)}>
              <ScanEye size={14} />
              有效快照
            </Button>
            <Button variant="secondary" size="sm" onClick={() => onPlaytest(persona!.id, form)}>
              <FlaskConical size={14} />
              试玩
            </Button>
          </>
        )}
        <span className="model-form-gap" />
        {isNew && (
          <Button variant="ghost" size="sm" onClick={onCancelNew}>
            取消
          </Button>
        )}
        {!isNew && (
          <Button variant="ghost" size="sm" disabled={!dirty || busy} onClick={reset}>
            重置
          </Button>
        )}
        <Button variant="primary" size="sm" busy={busy} disabled={!dirty && !isNew} onClick={save}>
          {isNew ? '创建人物卡' : '保存配置'}
        </Button>
      </div>
    </div>
  );
}

export default function PersonasPage() {
  const { connection, notify } = useWorkspace();
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [focused, setFocused] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importResult, setImportResult] = useState<PersonaImportPreview | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [play, setPlay] = useState<{ id: string; form: FormState } | null>(null);
  const [playInput, setPlayInput] = useState('');
  const [playResult, setPlayResult] = useState<PersonaPlaytest | null>(null);
  const [playBusy, setPlayBusy] = useState(false);
  const [effective, setEffective] = useState<PersonaEffective | null>(null);
  const [effectiveBusy, setEffectiveBusy] = useState(false);

  const load = useCallback(async () => {
    if (!connection) return;
    setLoading(true);
    setError('');
    try {
      setPersonas(await fetchPersonas(connection));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [connection]);
  useEffect(() => {
    void load();
  }, [load]);

  if (!connection) return null;
  const c = connection;

  const items: FocusGridItem[] = [
    ...(creating
      ? [
          {
            key: '__new__',
            ariaLabel: '新建人物卡',
            head: () => (
              <FocusCardTitle
                icon={<Plus size={19} />}
                name="新建人物卡"
                desc="填写后创建"
                aside={<Tag tone="violet">草稿</Tag>}
              />
            ),
            body: (f: boolean) => (
              <PersonaCardView
                persona={null}
                isNew
                focused={f}
                connection={c}
                onSaved={(p) => {
                  setCreating(false);
                  void load();
                  setFocused(p.id);
                  notify(`人物卡「${p.title}」已创建。`);
                }}
                onCancelNew={() => {
                  setCreating(false);
                  try {
                    localStorage.removeItem('whale.persona-draft.__new__');
                  } catch {
                    /* 忽略 */
                  }
                }}
                onPlaytest={() => undefined}
                onEffective={() => undefined}
                notify={notify}
              />
            ),
          } satisfies FocusGridItem,
        ]
      : []),
    ...personas.map(
      (p): FocusGridItem => ({
        key: p.id,
        ariaLabel: p.title,
        head: () => (
          <FocusCardTitle
            icon={<UserRound size={19} />}
            name={p.title}
            desc={p.summary || p.temperament || p.name}
            aside={
              <span className="persona-tags">
                {p.builtin && <Tag tone="slate">内置</Tag>}
                {p.category && <Tag tone="violet">{p.category}</Tag>}
              </span>
            }
          />
        ),
        body: (f: boolean) => (
          <PersonaCardView
            persona={p as PersonaDetail}
            isNew={false}
            focused={f}
            connection={c}
            onSaved={(saved) => {
              void load();
              notify(`人物卡「${saved.title}」已保存。`);
            }}
            onCancelNew={() => undefined}
            onPlaytest={(id, form) => {
              setPlay({ id, form });
              setPlayResult(null);
              setPlayInput('');
            }}
            onEffective={async (id) => {
              setEffectiveBusy(true);
              setEffective(null);
              try {
                setEffective(await personaEffective(c, id));
              } catch (e) {
                notify((e as Error).message, true);
                setEffectiveBusy(false);
              }
            }}
            notify={notify}
          />
        ),
      }),
    ),
  ];

  return (
    <div className="page-enter">
      <PageHeading
        page="personas"
        action={
          <>
            <Button variant="secondary" size="sm" icon={<Upload size={14} />} onClick={() => setImportOpen(true)}>
              导入卡片
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon={<Plus size={14} />}
              onClick={() => {
                setCreating(true);
                setFocused('__new__');
              }}
            >
              新建人物卡
            </Button>
          </>
        }
      />
      {error && <Banner tone="error">{error}</Banner>}
      {loading && <p className="muted padded">正在读取人物卡…</p>}
      {!loading && !personas.length && !creating && (
        <Empty title="还没有人物卡" text="新建或导入一张人物卡，为不同会话赋予不同人格。" />
      )}
      {(personas.length > 0 || creating) && (
        <FocusGrid items={items} focused={focused} onFocus={setFocused} />
      )}

      {importOpen && (
        <Modal title="导入人物卡" wide onClose={() => setImportOpen(false)}>
          {!importResult ? (
            <div className="model-form">
              <Field
                label="卡片内容"
                hint="支持 SillyTavern v1/v2 JSON 或纯文本；预览不写库。"
              >
                <Textarea
                  aria-label="卡片内容"
                  rows={9}
                  value={importText}
                  onChange={(e) => setImportText(e.target.value)}
                  placeholder='粘贴 SillyTavern JSON 或纯文本人物卡…'
                />
              </Field>
              <div className="model-form-actions">
                <span className="model-form-gap" />
                <Button
                  variant="primary"
                  size="sm"
                  busy={importBusy}
                  disabled={!importText.trim()}
                  onClick={async () => {
                    setImportBusy(true);
                    try {
                      setImportResult(await personaImportPreview(c, importText));
                    } catch (e) {
                      notify((e as Error).message, true);
                    } finally {
                      setImportBusy(false);
                    }
                  }}
                >
                  解析预览
                </Button>
              </div>
            </div>
          ) : (
            <div className="model-form">
              <Banner tone={importResult.conflicts.length ? 'warn' : 'info'}>
                建议卡 ID：<code>{importResult.suggested_card_id}</code>
                {importResult.conflicts.length > 0 && ` · 冲突 ${importResult.conflicts.length} 项`}
                {importResult.missing.length > 0 && ` · 缺字段 ${importResult.missing.length} 项`}
              </Banner>
              <div className="import-preview">
                {Object.entries(importResult.parsed).map(([k, v]) => (
                  <div className="import-row" key={k}>
                    <span className="import-field">{importResult.mapping[k] || k}</span>
                    <span className="import-value">{String(v).slice(0, 120) || '（空）'}</span>
                  </div>
                ))}
                {importResult.unmapped.map((u) => (
                  <div className="import-row" key={u.field}>
                    <span className="import-field">
                      {u.field} <Tag tone="amber">未映射</Tag>
                    </span>
                    <span className="import-value">{u.note}</span>
                  </div>
                ))}
              </div>
              {importResult.warnings.length > 0 && (
                <Banner tone="warn">{importResult.warnings.join('；')}</Banner>
              )}
              <div className="model-form-actions">
                <Button variant="ghost" size="sm" onClick={() => setImportResult(null)}>
                  返回修改
                </Button>
                <span className="model-form-gap" />
                <Button
                  variant="primary"
                  size="sm"
                  busy={importBusy}
                  onClick={async () => {
                    setImportBusy(true);
                    try {
                      const input: PersonaCardInput = {
                        title: importResult.parsed.title || importResult.parsed.name || '导入的人物卡',
                        ...Object.fromEntries(
                          Object.entries(importResult.parsed).filter(([, v]) => v),
                        ),
                      };
                      const saved = await createPersona(c, input);
                      setImportOpen(false);
                      setImportResult(null);
                      setImportText('');
                      void load();
                      setFocused(saved.id);
                      notify(`人物卡「${saved.title}」已导入。`);
                    } catch (e) {
                      notify((e as Error).message, true);
                    } finally {
                      setImportBusy(false);
                    }
                  }}
                >
                  确认导入
                </Button>
              </div>
            </div>
          )}
        </Modal>
      )}

      {play && (
        <Modal title="试玩对照" wide onClose={() => setPlay(null)}>
          <div className="model-form">
            <Field label="测试输入" hint="同一输入分别走基线与当前表单草稿，对照回复差异。">
              <Textarea
                aria-label="测试输入"
                rows={3}
                value={playInput}
                onChange={(e) => setPlayInput(e.target.value)}
                placeholder="说一句什么，看看两种人格怎么回…"
              />
            </Field>
            <div className="model-form-actions">
              <span className="model-form-gap" />
              <Button
                variant="primary"
                size="sm"
                busy={playBusy}
                disabled={!playInput.trim()}
                onClick={async () => {
                  setPlayBusy(true);
                  try {
                    setPlayResult(
                      await personaPlaytest(c, {
                        persona_id: play.id,
                        card: toInput(play.form),
                        input: playInput,
                      }),
                    );
                  } catch (e) {
                    notify((e as Error).message, true);
                  } finally {
                    setPlayBusy(false);
                  }
                }}
              >
                运行对照
              </Button>
            </div>
            {playResult && (
              <div className="playtest-grid">
                {(['baseline', 'trial'] as const).map((side) => (
                  <div className="playtest-side" key={side}>
                    <strong>{side === 'baseline' ? '当前生效（基线）' : '表单草稿（试用）'}</strong>
                    <small>{playResult[side].source}</small>
                    {playResult[side].error ? (
                      <Banner tone="error">{playResult[side].error}</Banner>
                    ) : (
                      <p>{playResult[side].reply}</p>
                    )}
                    {playResult[side].fields_used && (
                      <small className="muted">使用字段：{playResult[side].fields_used!.join('、')}</small>
                    )}
                  </div>
                ))}
              </div>
            )}
            {playResult && <p className="muted small-text">{playResult.note}</p>}
          </div>
        </Modal>
      )}

      {(effective || effectiveBusy) && (
        <Modal title="有效人格快照" wide onClose={() => { setEffective(null); setEffectiveBusy(false); }}>
          {effectiveBusy && (
            <p className="muted padded">
              <LoaderCircle size={15} className="spin" /> 正在读取…
            </p>
          )}
          {effective && (
            <div className="model-form">
              <div className="detail-meta">
                <span>来源</span>
                {effective.source}
                {effective.fallback_reason ? `（${effective.fallback_reason}）` : ''}
              </div>
              <div className="detail-meta">
                <span>内容指纹</span>
                <code>{effective.content_sha.slice(0, 16)}…</code>
              </div>
              <div className="detail-meta">
                <span>语音</span>
                {effective.voice.timbre_hint || '—'}
                {effective.voice.speaking_rate ? ` · ${effective.voice.speaking_rate}x` : ''}
              </div>
              <div className="span-list">
                {effective.dispositions.map((d) => (
                  <div className="span-row" key={d.field}>
                    <span className={`span-phase ${d.used_value ? 'ok' : 'skipped'}`}>{d.field}</span>
                    <span className="span-info">
                      <strong>{d.destination}</strong>
                      <small>{d.applied}</small>
                    </span>
                  </div>
                ))}
              </div>
              <Field label="进入模型上下文的确切内容">
                <Textarea readOnly rows={8} value={effective.model_context} aria-label="模型上下文" />
              </Field>
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
