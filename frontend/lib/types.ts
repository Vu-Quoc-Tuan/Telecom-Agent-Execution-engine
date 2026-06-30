export type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  optimistic?: boolean;
};

export type TimelineStep = {
  id: string;
  step_index: number;
  step_type: string;
  name: string;
  display_title?: string | null;
  display_order?: number | null;
  summary?: string | null;
  status: string;
  tool_name?: string | null;
  connector_name?: string | null;
  risk_level?: string | null;
  tool_status?: string | null;
  tool_input?: Record<string, unknown> | null;
  tool_output?: string | null;
  is_error?: boolean | null;
  output_truncated?: boolean | null;
};

export type ResourceItem = {
  id: string;
  name: string;
  kind: "ssh" | "clickhouse" | "postgres";
  status: "connected" | "disconnected";
  access: "read_only" | "read_write";
  region: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status?: "streaming" | "done" | "error";
  run_id?: string | null;
};

export type PersistedChatMessage = {
  id: string;
  session_id: string;
  run_id: string | null;
  role: "user" | "assistant" | "tool";
  content: string;
  status: "pending" | "streaming" | "completed" | "failed";
  sequence_no: number;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type PendingApproval = {
  approval_request_id: string;
  run_id?: string;
  tool_name?: string | null;
  tool_input?: Record<string, unknown> | null;
  risk_level?: string | null;
  required_confirmations: number;
  confirmation_count: number;
  status: "pending" | "approved" | "rejected";
};

export type ChatModelOption = {
  provider: string;
  model: string;
  label: string;
  description: string;
  available: boolean;
};

export type ChatSkillOption = {
  name: string;
  description: string;
};

export type ChatCapabilityOption = {
  name: string;
  connector: string;
  description: string;
};

export type ChatOptions = {
  default_model: Pick<ChatModelOption, "provider" | "model"> | null;
  models: ChatModelOption[];
  skills: ChatSkillOption[];
  capabilities: ChatCapabilityOption[];
};

export type SkillStatus = "uploaded" | "testing" | "ready" | "rejected";

export type SkillTelemetry = {
  call_count: number;
  average_latency_ms: number | null;
  error_rate: number;
  error_count: number;
  last_called_at: string | null;
};

export type SkillBundledFile = {
  encoding?: string;
  content?: string;
  media_type?: string;
  size?: number;
};

export type SkillScriptManifestEntry = {
  status?: string;
  script_hash?: string;
  purpose?: string;
  runtime?: Record<string, unknown>;
  input_schema?: Record<string, unknown>;
  output_contract?: Record<string, unknown>;
  smoke_test?: { arguments?: Record<string, unknown> };
  limits?: Record<string, unknown>;
  sandbox_result?: Record<string, unknown>;
};

export type SkillSummary = {
  id: string;
  name: string;
  description: string;
  skill_md: string;
  version: string;
  status: SkillStatus;
  is_malicious: boolean;
  security_review_log?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  frontmatter?: Record<string, unknown>;
  bundled_files?: Record<string, SkillBundledFile>;
  script_manifest?: Record<string, SkillScriptManifestEntry>;
  telemetry?: SkillTelemetry;
};
