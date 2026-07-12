import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AsrRecorder } from "@/components/voice/asr-recorder";
import { initialAsrState } from "@/lib/asr/machine";

const mocks = vi.hoisted(() => ({ useAsr: vi.fn() }));

vi.mock("@/hooks/use-asr", () => ({ useAsr: mocks.useAsr }));

describe("AsrRecorder", () => {
  it("forwards the real ASR confidence with the editable transcript", async () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["复习机器学习"],
        editableTranscript: "复习机器学习",
        confidence: 0.42,
        latencyMs: 180,
        sessionId: "voice-1",
        transcriptionId: "trn-1",
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });
    const onTranscriptChange = vi.fn();
    const onSourceChange = vi.fn();

    render(<AsrRecorder onTranscriptChange={onTranscriptChange} onSourceChange={onSourceChange} />);

    await waitFor(() => expect(onTranscriptChange).toHaveBeenCalledWith("复习机器学习", 0.42));
    expect(onSourceChange).toHaveBeenCalledWith({
      sessionId: "voice-1",
      transcriptionId: "trn-1",
      originalText: "复习机器学习",
    });
  });

  it("does not notify the same transcript again when a parent callback identity changes", async () => {
    mocks.useAsr.mockReturnValue({
      state: {
        ...initialAsrState,
        phase: "completed",
        finalSegments: ["复习机器学习"],
        editableTranscript: "复习机器学习",
        confidence: 0.9,
      },
      start: vi.fn(),
      pause: vi.fn(),
      resume: vi.fn(),
      stop: vi.fn(),
      reset: vi.fn(),
      editTranscript: vi.fn(),
    });

    function ParentWithInlineCallback() {
      const [notifications, setNotifications] = useState(0);
      return (
        <>
          <span data-testid="notifications">{notifications}</span>
          <AsrRecorder onTranscriptChange={() => setNotifications((count) => count + 1)} />
        </>
      );
    }

    render(<ParentWithInlineCallback />);
    await waitFor(() => expect(screen.getByTestId("notifications")).toHaveTextContent("1"));
  });
});
