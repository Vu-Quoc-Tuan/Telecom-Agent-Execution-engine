import { useCallback, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { TimelineStep } from "@/lib/types";

type RunTimelineResponse = {
  run_id: string;
  status: string;
  model?: string | null;
  steps: TimelineStep[];
};

export function useRunTimeline({
  onActivate,
  onError,
}: {
  onActivate: () => void;
  onError: (message: string) => void;
}) {
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [selectedTimelineRunId, setSelectedTimelineRunId] = useState<string | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const selectedTimelineRunIdRef = useRef<string | null>(null);

  const clearTimeline = useCallback(() => {
    setSteps([]);
    setSelectedTimelineRunId(null);
    selectedTimelineRunIdRef.current = null;
  }, []);

  const selectTimelineRunId = useCallback((runId: string | null) => {
    setSelectedTimelineRunId(runId);
    selectedTimelineRunIdRef.current = runId;
  }, []);

  const getSelectedTimelineRunId = useCallback(() => selectedTimelineRunIdRef.current, []);

  const applyTimelineUpdate = useCallback((runId: string, nextSteps: TimelineStep[]) => {
    const selectedRunId = selectedTimelineRunIdRef.current;
    if (!selectedRunId || selectedRunId === runId) {
      setSteps(nextSteps);
    }
  }, []);

  const loadRunTimeline = useCallback(
    async (runId: string | null | undefined) => {
      if (!runId) return;
      onActivate();
      selectTimelineRunId(runId);
      setTimelineLoading(true);
      try {
        const timeline = await apiFetch<RunTimelineResponse>(`/runs/${runId}/timeline`);
        setSteps(timeline.steps);
      } catch (exc) {
        onError(exc instanceof Error ? exc.message : "Không tải được mạch tư duy của run.");
      } finally {
        setTimelineLoading(false);
      }
    },
    [onActivate, onError, selectTimelineRunId],
  );

  return {
    steps,
    selectedTimelineRunId,
    timelineLoading,
    clearTimeline,
    selectTimelineRunId,
    getSelectedTimelineRunId,
    applyTimelineUpdate,
    loadRunTimeline,
  };
}
