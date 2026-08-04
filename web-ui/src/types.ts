/**
 * Background: The backend has ~85 endpoints returning JSON, with fields that may be added or
 * removed across versions; the frontend requires "never crash on missing fields".
 * Design intent: All entity interface fields are declared optional; page layer uses ?? / ?. for safety,
 * and TypeScript enforces that every access considers the missing case at compile time.
 * Key constraint: Do not make fields required here; add types for new endpoints in this file first.
 */

export interface MemorySummary {
  id?: string;
  title?: string;
  tags?: string[];
  priority?: number;
  version?: number;
  created_at?: string;
  updated_at?: string;
  source?: string;
  status?: string;
  content_preview?: string;
  content?: string;
  score?: number;
}

export interface MemoryListResponse {
  memories?: MemorySummary[];
  total?: number;
}

export interface MemoryDetail extends MemorySummary {
  content?: string;
}

export interface HistoryEntry {
  version?: number;
  created_at?: string;
  updated_at?: string;
  /** The timestamp field name used by backend MemoryHistory */
  changed_at?: string;
  title?: string;
  change?: string;
  action?: string;
}

export interface TrunkItem {
  id?: string;
  index?: number;
  /** The in-segment order field name used by backend Trunk */
  order?: number;
  content?: string;
  text?: string;
  summary?: string;
  /** AI-extracted semantic tags (people/places/time/keywords etc.) */
  meta_tags?: string[];
  meta_status?: string;
}

export interface SearchResultItem extends MemorySummary {
  snippet?: string;
  match?: string;
}

export interface TagStat {
  tag?: string;
  name?: string;
  count?: number;
}

export interface TagTreeNode {
  name?: string;
  tag?: string;
  full_tag?: string;
  path?: string;
  count?: number;
  children?: TagTreeNode[];
}

export interface StatsResponse {
  total_memories?: number;
  active_memories?: number;
  archived_memories?: number;
  total_tags?: number;
  total_trunks?: number;
  [key: string]: unknown;
}

export interface SmartChunk {
  title?: string;
  content?: string;
  text?: string;
  tags?: string[];
}

export interface ProviderConfig {
  name?: string;
  category?: string;
  api_type?: "openai_compatible" | "anthropic" | "gemini" | string;
  base_url?: string;
  api_key_env?: string;
  embedding_model?: string;
  chat_model?: string;
  has_api_key?: boolean;
}

export interface AppConfig {
  providers?: Record<string, ProviderConfig>;
  provider_catalog?: Record<string, ProviderConfig>;
  active?: { embedding_provider?: string; chat_provider?: string };
  search?: {
    semantic?: { enabled?: boolean; min_similarity?: number; min_similarity_max?: number };
  };
  automation?: {
    arbitration_enabled?: boolean;
    capture_enabled?: boolean;
    dream_auto_activate?: boolean;
  };
  server?: { port?: number };
}

/** One entry of the memory upkeep trail (write-time tidy decisions). */
export interface UpkeepLogItem {
  id: number;
  new_memory_id: string;
  action: "keep_both" | "supersede" | "duplicate" | string;
  target_ids: string[];
  archived_ids: string[];
  reason?: string;
  created_at?: string;
  titles?: Record<string, string>;
}

export interface VectorRebuildStatus {
  running?: boolean;
  phase?: string;
  current?: number;
  total?: number;
  memory_done?: number;
  trunk_done?: number;
  completed?: boolean;
  error?: string | null;
  percent?: number;
}

export interface TokenItem {
  id?: string | number;
  name?: string;
  prefix?: string;
  token?: string;
  created_at?: string;
  last_used_at?: string;
  revoked?: boolean;
  scopes?: string[];
}

export interface LogItem {
  id?: string | number;
  method?: string;
  path?: string;
  status?: number;
  status_code?: number;
  duration_ms?: number;
  created_at?: string;
  timestamp?: string;
  request_body?: unknown;
  response_body?: unknown;
  [key: string]: unknown;
}

export interface GraphNode {
  id?: string;
  name?: string;
  label?: string;
  type?: string;
  count?: number;
  [key: string]: unknown;
}

export interface GraphEdge {
  source?: string;
  target?: string;
  relation?: string;
  label?: string;
  weight?: number;
  [key: string]: unknown;
}

export interface EmbeddingPoint {
  id?: string;
  memory_id?: string;
  document_id?: string;
  document_title?: string;
  order?: number;
  title?: string;
  x?: number;
  y?: number;
  z?: number;
  tag?: string;
  tags?: string[];
  cluster?: number | string;
  priority?: number;
  source?: string;
  is_image?: boolean;
  content_type?: string;
  created_at?: string | null;
  [key: string]: unknown;
}

export interface TimelineEvent {
  id?: string | number;
  // Backend time_events table fields
  event_summary?: string;
  original_text?: string;
  absolute_time?: string;
  event_type?: string;
  status?: string;
  completed_at?: string | null;
  is_expired?: boolean;
  document_id?: string;
  document_title?: string;
  trunk_id?: string;
  [key: string]: unknown;
}

// ---- User profile layer (/api/profile) ----

export interface ProfileFieldDef {
  key: string;
  label: string;
  required: boolean;
  hint?: string;
}

export interface ProfileFields {
  schema: ProfileFieldDef[];
  values: Record<string, string>;
  sources: Record<string, { source: string; updated_at?: string }>;
  missing_required: string[];
}

export interface ProfileFieldHistoryItem {
  id: number;
  key: string;
  value: string;
  source?: string;
  archived_at?: string;
}

export interface ProfileClaim {
  id: number;
  version_id: number;
  tier: string;
  text: string;
  sources: Array<string | number>;
  source_kind: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  verified_at?: string | null;
}

export interface DreamSuggestion {
  suggested_at: string;
  reasons: string[];
}

export interface ProfileStatus {
  enabled: boolean;
  active_version_id: number;
  claim_counts: Record<string, number>;
  pending_issues: number;
  last_distill?: { day?: string; at?: string; added?: number } | null;
  last_daily_run?: { day?: string; at?: string } | null;
  dream_suggestion?: DreamSuggestion | null;
  missing_required_fields: string[];
}

export interface DreamItem {
  id: number;
  status: string;
  scope?: string;
  instructions?: string;
  input_version_id?: number;
  output_version_id?: number | null;
  usage_tokens?: number;
  trigger_reason?: string;
  error?: string | null;
  created_at?: string;
  ended_at?: string | null;
}

export interface DreamDiff {
  version_id: number;
  base_version_id: number | null;
  added: ProfileClaim[];
  removed: ProfileClaim[];
  modified: Array<{ before: ProfileClaim; after: ProfileClaim }>;
  unchanged_count: number;
}
