import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  BarChart3,
  BookOpenText,
  CalendarClock,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  FileSearch,
  ListChecks,
  LoaderCircle,
  Moon,
  RefreshCw,
  ShieldAlert,
  Sun,
  Target,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { getDashboard, getReport } from './api';
import type { DashboardSnapshot, ReportDocument, UnknownRecord } from './types';

type View = 'overview' | 'judgments' | 'reports';
type Theme = 'light' | 'dark';

function storedTheme(): Theme {
  try {
    return window.localStorage?.getItem('stock-monitor-theme') === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function rememberTheme(theme: Theme): void {
  try {
    window.localStorage?.setItem('stock-monitor-theme', theme);
  } catch {
    // Storage can be disabled by browser privacy settings; the active theme still works.
  }
}

const navItems: Array<{ id: View; label: string; icon: typeof BarChart3 }> = [
  { id: 'overview', label: '总览', icon: BarChart3 },
  { id: 'judgments', label: '判断台账', icon: ListChecks },
  { id: 'reports', label: '报告库', icon: BookOpenText },
];

function text(value: unknown, fallback = '—'): string {
  if (typeof value === 'string' && value.trim()) return value;
  if (typeof value === 'number') return String(value);
  return fallback;
}

function displayLine(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const item = value as UnknownRecord;
    return [text(item.symbol, ''), text(item.name, ''), text(item.note, '')].filter(Boolean).join(' · ');
  }
  return String(value ?? '');
}

function StatusPill({ tone = 'neutral', children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`status-pill status-pill--${tone}`}>{children}</span>;
}

function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <CircleDot aria-hidden="true" />
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
  );
}

function OpportunityCard({ item, level }: { item: UnknownRecord; level: string }) {
  return (
    <article className="opportunity-card">
      <div className="opportunity-card__head">
        <div>
          <span className="eyebrow">{text(item.symbol, level)}</span>
          <h4>{text(item.name, '未命名标的')}</h4>
        </div>
        {item.price !== undefined && <strong className="price">{text(item.price)}</strong>}
      </div>
      <div className="tag-row">
        {item.class !== undefined && <StatusPill>{text(item.class)}</StatusPill>}
        {item.wyckoff !== undefined && <StatusPill tone="info">{text(item.wyckoff)}</StatusPill>}
        {item.missing !== undefined && <StatusPill tone="warning">缺 {text(item.missing)}</StatusPill>}
      </div>
      {item.why !== undefined && <p className="card-reason">{text(item.why)}</p>}
      <dl className="decision-lines">
        {item.trigger !== undefined && (
          <div>
            <dt><TrendingUp aria-hidden="true" />触发</dt>
            <dd>{text(item.trigger)}</dd>
          </div>
        )}
        {item.invalidate !== undefined && (
          <div>
            <dt><TrendingDown aria-hidden="true" />失效</dt>
            <dd>{text(item.invalidate)}</dd>
          </div>
        )}
      </dl>
      {item.caveat !== undefined && <p className="caveat">{text(item.caveat)}</p>}
    </article>
  );
}

function ListPanel({ title, icon: Icon, items, empty = '当前无条目', tone = '' }: {
  title: string;
  icon: typeof Target;
  items: unknown[];
  empty?: string;
  tone?: string;
}) {
  return (
    <section className={`panel list-panel ${tone}`}>
      <div className="panel__head">
        <h3><Icon aria-hidden="true" />{title}</h3>
        <span className="count">{items.length}</span>
      </div>
      {items.length ? (
        <ul className="plain-list">
          {items.map((item, index) => <li key={`${displayLine(item)}-${index}`}>{displayLine(item)}</li>)}
        </ul>
      ) : <EmptyState title={empty} />}
    </section>
  );
}

function Overview({ snapshot }: { snapshot: DashboardSnapshot }) {
  const stale = snapshot.sources.focus_is_stale || snapshot.sources.analysis_is_stale;
  const warnings = snapshot.data_quality.warnings.length + snapshot.data_quality.limitations.length;

  return (
    <main id="main-content" className="content" tabIndex={-1}>
      <section className="hero panel">
        <div>
          <span className="eyebrow">市场状态</span>
          <h1>{snapshot.market.regime || '未知'}</h1>
          <p>{snapshot.market.stance || '等待有效的状态输入。'}</p>
        </div>
        <div className="hero__meta">
          <StatusPill tone={stale ? 'warning' : 'positive'}>
            {stale ? '来源时点不一致' : '数据日期一致'}
          </StatusPill>
          <span>{snapshot.market.trajectory || '暂无轨迹'}</span>
        </div>
      </section>

      <section className="metric-grid" aria-label="核心指标">
        <article className="metric-card"><CalendarClock /><span>报告日期</span><strong>{snapshot.sources.report_date ?? '—'}</strong></article>
        <article className="metric-card"><Target /><span>A 档机会</span><strong>{snapshot.opportunities.a.items.length}</strong></article>
        <article className="metric-card"><ListChecks /><span>待验证判断</span><strong>{snapshot.judgments.open.length}</strong></article>
        <article className="metric-card"><ShieldAlert /><span>数据警告</span><strong>{warnings}</strong></article>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div><span className="eyebrow">行动优先级</span><h2>机会分档</h2></div>
          <p>触发条件成立前，候选不等于指令。</p>
        </div>
        <div className="tier-grid">
          <section className="tier tier--a">
            <div className="tier__title"><StatusPill tone="positive">A</StatusPill><h3>可执行</h3><span>{snapshot.opportunities.a.items.length}</span></div>
            {snapshot.opportunities.a.items.length ? snapshot.opportunities.a.items.map((item, index) => <OpportunityCard key={`a-${index}`} item={item} level="A" />) : <EmptyState title="今日无 A 档" detail={snapshot.opportunities.a.empty_reason} />}
          </section>
          <section className="tier tier--b">
            <div className="tier__title"><StatusPill tone="warning">B</StatusPill><h3>等待入场</h3><span>{snapshot.opportunities.b.items.length}</span></div>
            {snapshot.opportunities.b.items.length ? snapshot.opportunities.b.items.map((item, index) => <OpportunityCard key={`b-${index}`} item={item} level="B" />) : <EmptyState title="今日无 B 档" />}
          </section>
          <section className="tier tier--c">
            <div className="tier__title"><StatusPill tone="info">C</StatusPill><h3>仅观察</h3><span>{snapshot.opportunities.c.items.length}</span></div>
            {snapshot.opportunities.c.items.length ? snapshot.opportunities.c.items.map((item, index) => <OpportunityCard key={`c-${index}`} item={item} level="C" />) : <EmptyState title="今日无 C 档" />}
          </section>
        </div>
      </section>

      <section className="two-column">
        <ListPanel title="已确认主线" icon={CheckCircle2} items={snapshot.market.mainlines.confirmed} />
        <ListPanel title="待确认主线" icon={CircleDot} items={snapshot.market.mainlines.pending} />
        <ListPanel title="核心关注" icon={Target} items={snapshot.focus.core} />
        <ListPanel title="观察池" icon={FileSearch} items={snapshot.focus.observation} />
        <ListPanel title="移出 / 降级" icon={TrendingDown} items={snapshot.exits} tone="panel--danger" />
        <ListPanel title="回避清单" icon={ShieldAlert} items={snapshot.focus.avoid} tone="panel--danger" />
      </section>

      <section className="two-column">
        <ListPanel title="数据质量警告" icon={AlertTriangle} items={[...snapshot.data_quality.warnings, ...snapshot.data_quality.limitations]} tone="panel--warning" />
        <ListPanel title="派发与风险信号" icon={ShieldAlert} items={snapshot.risks.distribution_alerts} tone="panel--warning" />
      </section>
    </main>
  );
}

function JudgmentDesk({ snapshot }: { snapshot: DashboardSnapshot }) {
  return (
    <main id="main-content" className="content" tabIndex={-1}>
      <div className="page-heading"><div><span className="eyebrow">可证伪记录</span><h1>判断台账</h1></div><p>把结论、时间窗和作废条件放在同一处。</p></div>
      <section className="panel">
        <div className="panel__head"><h2><CircleDot />待验证</h2><span className="count">{snapshot.judgments.open.length}</span></div>
        {snapshot.judgments.open.length ? (
          <div className="judgment-grid">
            {snapshot.judgments.open.map((item, index) => (
              <article className="judgment-card" key={text(item.id, String(index))}>
                <div className="judgment-card__head"><StatusPill tone="info">{text(item.id, `J${index + 1}`)}</StatusPill><span>{text(item.horizon_days, '?')} 交易日</span></div>
                <h3>{text(item.claim, '暂无判断内容')}</h3>
                <p><strong>证伪条件</strong>{text(item.falsifiable, '未设定')}</p>
              </article>
            ))}
          </div>
        ) : <EmptyState title="暂无待验证判断" />}
      </section>
      <section className="panel">
        <div className="panel__head"><h2><CheckCircle2 />上期验证</h2><span className="count">{snapshot.judgments.verified.length}</span></div>
        {snapshot.judgments.verified.length ? (
          <div className="table-wrap"><table><thead><tr><th>ID</th><th>状态</th><th>复盘记录</th></tr></thead><tbody>{snapshot.judgments.verified.map((item, index) => <tr key={`${text(item.id)}-${index}`}><td>{text(item.id)}</td><td><StatusPill tone={text(item.status).includes('确认') ? 'positive' : 'warning'}>{text(item.status)}</StatusPill></td><td>{text(item.note)}</td></tr>)}</tbody></table></div>
        ) : <EmptyState title="暂无上期验证记录" />}
      </section>
      <ListPanel title="近期风险日历" icon={CalendarClock} items={snapshot.risks.events} />
    </main>
  );
}

function ReportLibrary({ snapshot }: { snapshot: DashboardSnapshot }) {
  const [document, setDocument] = useState<ReportDocument | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const openDocument = async (date: string, kind: 'report' | 'analysis') => {
    setLoading(true);
    setError('');
    try {
      setDocument(await getReport(date, kind));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '报告读取失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <main id="main-content" className="content report-layout" tabIndex={-1}>
      <aside className="panel report-index">
        <div className="panel__head"><h1>报告库</h1><span className="count">{snapshot.reports.length}</span></div>
        {snapshot.reports.length ? (
          <div className="report-list">
            {snapshot.reports.map((item) => (
              <section key={item.date} className="report-row">
                <strong>{item.date}</strong>
                <div>
                  {item.has_report && <button type="button" onClick={() => openDocument(item.date, 'report')}>日报 <ChevronRight /></button>}
                  {item.has_analysis && <button type="button" onClick={() => openDocument(item.date, 'analysis')}>分析 <ChevronRight /></button>}
                </div>
              </section>
            ))}
          </div>
        ) : <EmptyState title="尚无可用报告" />}
      </aside>
      <section className="panel report-reader" aria-live="polite">
        {loading && <div className="center-state"><LoaderCircle className="spin" /><p>正在读取报告…</p></div>}
        {!loading && error && <EmptyState title="无法打开报告" detail={error} />}
        {!loading && !error && !document && <EmptyState title="选择一份报告" detail="原始 Markdown 会在这里以只读方式呈现。" />}
        {!loading && document && (
          <>
            <div className="reader-head"><div><span className="eyebrow">{document.kind === 'report' ? '每日报告' : '分析记录'}</span><h2>{document.date}</h2></div><StatusPill>只读</StatusPill></div>
            <article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{document.content}</ReactMarkdown></article>
          </>
        )}
      </section>
    </main>
  );
}

function LoadingScreen() {
  return <div className="app-state"><LoaderCircle className="spin" /><h1>正在组装决策工作台</h1><p>读取最新报告、聚焦清单与判断台账…</p></div>;
}

export default function App() {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(null);
  const [view, setView] = useState<View>('overview');
  const [error, setError] = useState('');
  const [theme, setTheme] = useState<Theme>(storedTheme);

  const load = useCallback(async () => {
    setError('');
    try {
      setSnapshot(await getDashboard());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : '数据读取失败');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    rememberTheme(theme);
  }, [theme]);

  const title = useMemo(() => navItems.find((item) => item.id === view)?.label ?? '总览', [view]);

  if (!snapshot && !error) return <LoadingScreen />;
  if (!snapshot) return <div className="app-state"><AlertTriangle /><h1>工作台连接失败</h1><p>{error}</p><button className="primary-button" type="button" onClick={() => void load()}><RefreshCw />重试</button></div>;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主内容</a>
      <aside className="sidebar">
        <div className="brand"><div className="brand__mark"><TrendingUp /></div><div><strong>Stock Monitor</strong><span>Decision Workbench</span></div></div>
        <nav aria-label="工作台导航">
          {navItems.map(({ id, label, icon: Icon }) => <button type="button" key={id} className={view === id ? 'active' : ''} aria-current={view === id ? 'page' : undefined} onClick={() => setView(id)}><Icon /><span>{label}</span></button>)}
        </nav>
        <div className="sidebar__foot"><StatusPill tone="positive"><CircleDot />只读模式</StatusPill><span>Schema {snapshot.schema_version}</span></div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div><span className="eyebrow">Stock Monitor Workbench</span><strong>{title}</strong></div>
          <div className="topbar__actions">
            <span className="as-of">数据截至 <strong>{snapshot.as_of_date ?? '暂无'}</strong></span>
            <button className="icon-button" type="button" aria-label={theme === 'light' ? '切换至深色模式' : '切换至浅色模式'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>{theme === 'light' ? <Moon /> : <Sun />}</button>
            <button className="icon-button" type="button" aria-label="刷新数据" onClick={() => void load()}><RefreshCw /></button>
          </div>
        </header>

        {(snapshot.sources.focus_is_stale || snapshot.sources.analysis_is_stale) && (
          <div className="stale-banner" role="status"><AlertTriangle /><strong>状态日期落后</strong><span>报告 {snapshot.sources.report_date ?? '—'} · 分析 {snapshot.sources.analysis_date ?? '—'} · 聚焦 {snapshot.sources.focus_date ?? '—'}</span></div>
        )}
        {view === 'overview' && <Overview snapshot={snapshot} />}
        {view === 'judgments' && <JudgmentDesk snapshot={snapshot} />}
        {view === 'reports' && <ReportLibrary snapshot={snapshot} />}
      </div>
    </div>
  );
}
