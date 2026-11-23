export type AnalysisStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'NO_MATCH' | 'FAILED' | 'SKIPPED' | 'STALE';
export type ReviewStatus = 'NOT_REQUIRED' | 'PENDING' | 'APPROVED' | 'REJECTED';
export type InjectionStatus = 'NOT_REQUESTED' | 'PENDING' | 'IN_PROGRESS' | 'SUCCEEDED' | 'FAILED' | 'RECOVERY_REQUIRED';
export type JobStatus = 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled' | 'interrupted';

export interface SegmentResult {
  name: string;
  start: number;
  end: number;
  /** Raw chroma-correlation score. This is not a probability or percentage. */
  score: number;
}

export interface EpisodeRecord {
  id: number | null;
  file_name: string;
  file_path: string;
  relative_path: string;
  folder_path: string;
  folder_segments: string[];
  display_name: string;
  analysis_status: AnalysisStatus;
  review_status: ReviewStatus;
  injection_status: InjectionStatus;
  op: SegmentResult | null;
  ed: SegmentResult | null;
  is_anomaly_op: boolean;
  is_anomaly_ed: boolean;
  legacy_result?: boolean;
  error?: string | null;
}

export interface JobRecord {
  id: string;
  kind: 'series' | 'individual';
  series_name: string | null;
  status: JobStatus;
  stage: string;
  progress: number;
  current_item: string | null;
  total_items: number;
  items: Array<{ item_key: string; file_name: string; status: string; progress: number; result?: unknown }>;
  error?: string | null;
}
