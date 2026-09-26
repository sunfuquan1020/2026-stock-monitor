export type UnknownRecord = Record<string, unknown>;

export interface SourceState {
  report_date: string | null;
  analysis_date: string | null;
  focus_date: string | null;
  judgment_date: string | null;
  focus_is_stale: boolean;
  analysis_is_stale: boolean;
}

export interface OpportunityGroup {
  items: UnknownRecord[];
  empty_reason?: string;
}

export interface ReportIndexItem {
  date: string;
  has_report: boolean;
  has_analysis: boolean;
  has_judgment: boolean;
}

export interface DashboardSnapshot {
  schema_version: string;
  as_of_date: string | null;
  sources: SourceState;
  market: {
    regime: string;
    regime_adopted: string;
    trajectory: string;
    stance: string;
    switch_note: string;
    mainlines: { confirmed: unknown[]; pending: unknown[] };
  };
  opportunities: {
    a: OpportunityGroup;
    b: OpportunityGroup;
    c: OpportunityGroup;
  };
  exits: unknown[];
  focus: { core: unknown[]; observation: unknown[]; avoid: unknown[] };
  judgments: { verified: UnknownRecord[]; open: UnknownRecord[] };
  risks: { events: UnknownRecord[]; distribution_alerts: unknown[] };
  data_quality: { as_of: string; warnings: unknown[]; limitations: unknown[] };
  reports: ReportIndexItem[];
}

export interface ReportDocument {
  date: string;
  kind: 'report' | 'analysis';
  content: string;
}
