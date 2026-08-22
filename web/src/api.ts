import type { DashboardSnapshot, ReportDocument } from './types';

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!response.ok) {
    throw new Error(`请求失败（${response.status}）`);
  }
  return response.json() as Promise<T>;
}

export function getDashboard(): Promise<DashboardSnapshot> {
  return getJson<DashboardSnapshot>('/api/v1/dashboard');
}

export function getReport(date: string, kind: 'report' | 'analysis'): Promise<ReportDocument> {
  return getJson<ReportDocument>(`/api/v1/reports/${encodeURIComponent(date)}/${kind}`);
}
