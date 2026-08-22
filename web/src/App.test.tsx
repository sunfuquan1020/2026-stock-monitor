import { render, screen } from '@testing-library/react';
import { expect, test, vi } from 'vitest';

import App from './App';

const snapshot = {
  schema_version: '1.0',
  as_of_date: '2026-08-15',
  sources: {
    report_date: '2026-08-15',
    analysis_date: '2026-08-14',
    focus_date: '2026-08-14',
    judgment_date: '2026-08-14',
    focus_is_stale: true,
    analysis_is_stale: true,
  },
  market: {
    regime: '震荡',
    regime_adopted: '震荡',
    trajectory: '熄火 → 震荡',
    stance: '控制追高',
    switch_note: '',
    mainlines: { confirmed: [], pending: ['半导体 — 待确认'] },
  },
  opportunities: {
    a: { items: [], empty_reason: '缺少量能确认' },
    b: { items: [{ symbol: '688981', name: '中芯国际', trigger: '量比>1.3' }] },
    c: { items: [] },
  },
  exits: [],
  focus: { core: [], observation: [], avoid: [] },
  judgments: { verified: [], open: [] },
  risks: { events: [], distribution_alerts: [] },
  data_quality: { as_of: '', warnings: ['美股未更新'], limitations: [] },
  reports: [],
};

test('renders regime, stale state, and opportunity discipline', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot }));

  render(<App />);

  expect(await screen.findByRole('heading', { name: '震荡' })).toBeInTheDocument();
  expect(screen.getByText('状态日期落后')).toBeInTheDocument();
  expect(screen.getByText('今日无 A 档')).toBeInTheDocument();
  expect(screen.getByText('缺少量能确认')).toBeInTheDocument();
  expect(screen.getByRole('navigation', { name: '工作台导航' })).toBeInTheDocument();
});
