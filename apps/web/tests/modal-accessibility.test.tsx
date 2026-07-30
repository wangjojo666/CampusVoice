import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { Modal } from "@/components/ui/modal";

afterEach(cleanup);

function ModalHarness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开对话框
      </button>
      <button type="button">背景操作</button>
      <Modal
        open={open}
        title="编辑日程"
        description="检查焦点和背景隔离"
        onClose={() => setOpen(false)}
      >
        <input autoFocus aria-label="标题" />
        <button
          type="button"
          onKeyDown={(event) => {
            if (event.key === "Escape") event.preventDefault();
          }}
        >
          内部消费 Escape
        </button>
        <button type="button">最后操作</button>
        <input type="hidden" aria-label="隐藏输入" />
        <button type="button" tabIndex={-2}>
          负序操作
        </button>
      </Modal>
    </>
  );
}

function StackedModalHarness() {
  const [outerOpen, setOuterOpen] = useState(false);
  const [innerOpen, setInnerOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOuterOpen(true)}>
        打开外层
      </button>
      <Modal open={outerOpen} title="外层对话框" onClose={() => setOuterOpen(false)}>
        <button type="button" onClick={() => setInnerOpen(true)}>
          打开内层
        </button>
      </Modal>
      <Modal open={innerOpen} title="内层对话框" onClose={() => setInnerOpen(false)}>
        <button type="button">内层操作</button>
        <button
          type="button"
          onClick={() => {
            setInnerOpen(false);
            setOuterOpen(false);
          }}
        >
          关闭整组
        </button>
      </Modal>
    </>
  );
}

function ReplacingContentModalHarness() {
  const [open, setOpen] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open changing dialog
      </button>
      <Modal open={open} title="Changing dialog" onClose={() => setOpen(false)}>
        {reviewing ? (
          <div>
            <p>Review the replacement content</p>
            <button type="button">Confirm replacement</button>
          </div>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setReviewing(true);
            }}
          >
            <button type="submit">Review replacement</button>
          </form>
        )}
      </Modal>
    </>
  );
}

describe("Modal accessibility boundary", () => {
  it("enters and traps focus, inerts the background, locks scrolling, and restores focus", async () => {
    const user = userEvent.setup();
    const originalOverflow = document.body.style.overflow;
    const { container } = render(<ModalHarness />);
    const opener = screen.getByRole("button", { name: "打开对话框" });

    await user.click(opener);

    const dialog = await screen.findByRole("dialog");
    const titleId = dialog.getAttribute("aria-labelledby");
    const descriptionId = dialog.getAttribute("aria-describedby");
    expect(titleId).toBeTruthy();
    expect(titleId).not.toBe("modal-title");
    expect(document.getElementById(titleId!)).toHaveTextContent("编辑日程");
    expect(document.getElementById(descriptionId!)).toHaveTextContent("检查焦点和背景隔离");
    expect(container).toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    await waitFor(() => expect(screen.getByRole("textbox", { name: "标题" })).toHaveFocus());

    screen.getByRole("button", { name: "最后操作" }).focus();
    await user.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "关闭" })).toHaveFocus();

    await user.keyboard("{Shift>}{Tab}{/Shift}");
    const lastAction = screen.getByRole("button", { name: "最后操作" });
    expect(lastAction).toHaveFocus();

    const consumedEscape = screen.getByRole("button", { name: "内部消费 Escape" });
    consumedEscape.focus();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    lastAction.focus();
    fireEvent.keyDown(lastAction, { key: "Escape", isComposing: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(container).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe(originalOverflow);
    expect(opener).toHaveFocus();
  });

  it("keeps focus inside when the focused modal content is replaced", async () => {
    const user = userEvent.setup();
    render(<ReplacingContentModalHarness />);

    await user.click(screen.getByRole("button", { name: "Open changing dialog" }));
    const dialog = await screen.findByRole("dialog", { name: "Changing dialog" });
    const review = screen.getByRole("button", { name: "Review replacement" });
    review.focus();
    expect(review).toHaveFocus();

    await user.keyboard("{Enter}");
    await screen.findByText("Review the replacement content");

    await waitFor(() => {
      expect(document.activeElement).toBeInstanceOf(HTMLElement);
      expect(dialog.contains(document.activeElement)).toBe(true);
    });
  });

  it("lets only the top modal handle Escape and keeps the outer modal isolated", async () => {
    const user = userEvent.setup();
    const originalOverflow = document.body.style.overflow;
    const { container } = render(<StackedModalHarness />);
    const outerOpener = screen.getByRole("button", { name: "打开外层" });

    await user.click(outerOpener);
    const outerDialog = await screen.findByRole("dialog", { name: "外层对话框" });
    const innerOpener = screen.getByRole("button", { name: "打开内层" });
    await user.click(innerOpener);

    const innerDialog = await screen.findByRole("dialog", { name: "内层对话框" });
    expect(outerDialog.parentElement).toHaveAttribute("inert");
    expect(container).toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    await waitFor(() => expect(innerDialog.getElementsByTagName("button")[0]).toHaveFocus());

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "内层对话框" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "外层对话框" })).toBeInTheDocument();
    expect(outerDialog.parentElement).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe("hidden");
    expect(innerOpener).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(container).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe(originalOverflow);
    expect(outerOpener).toHaveFocus();
  });
  it("falls back to the page opener when the whole modal stack closes in one render", async () => {
    const user = userEvent.setup();
    const originalOverflow = document.body.style.overflow;
    const { container } = render(<StackedModalHarness />);
    const outerOpener = screen.getByRole("button", { name: "打开外层" });

    await user.click(outerOpener);
    await user.click(await screen.findByRole("button", { name: "打开内层" }));
    await screen.findByRole("dialog", { name: "内层对话框" });
    await user.click(screen.getByRole("button", { name: "关闭整组" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(container).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe(originalOverflow);
    expect(outerOpener).toHaveFocus();
  });

  it("cleans global isolation and scroll state when an open modal stack unmounts", async () => {
    const user = userEvent.setup();
    const originalOverflow = document.body.style.overflow;
    const { container, unmount } = render(<StackedModalHarness />);

    await user.click(screen.getByRole("button", { name: "打开外层" }));
    await user.click(await screen.findByRole("button", { name: "打开内层" }));
    await screen.findByRole("dialog", { name: "内层对话框" });
    expect(document.body.style.overflow).toBe("hidden");

    unmount();

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(container).not.toHaveAttribute("inert");
    expect(document.body.style.overflow).toBe(originalOverflow);
  });
});
