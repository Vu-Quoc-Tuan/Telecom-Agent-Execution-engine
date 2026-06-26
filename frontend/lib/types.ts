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
  summary?: string | null;
  status: string;
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
  reason: string;
  status: "pending" | "approved" | "rejected";
  note?: string;
};

export type SkillStatus = "uploaded" | "testing" | "ready" | "rejected";

export type SkillSummary = {
  id: string;
  name: string;
  description: string;
  version: string;
  status: SkillStatus;
  is_malicious: boolean;
  security_review_log?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  frontmatter?: Record<string, unknown>;
  bundled_files?: Record<string, unknown>;
};
