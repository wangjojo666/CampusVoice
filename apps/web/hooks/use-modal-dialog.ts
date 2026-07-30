"use client";

import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button",
  "input:not([type='hidden'])",
  "select",
  "textarea",
  "summary",
  "[contenteditable]:not([contenteditable='false'])",
  "audio[controls]",
  "video[controls]",
  "iframe",
  "[tabindex]",
].join(",");

type ModalEntry = {
  id: symbol;
  backdrop: HTMLDivElement;
  dialog: HTMLElement;
  returnFocusCandidates: HTMLElement[];
};

const modalStack: ModalEntry[] = [];
const managedInertElements = new Set<HTMLElement>();
let bodyOverflowBeforeModal: string | null = null;
let bodyObserver: MutationObserver | null = null;

function topModal() {
  return modalStack.at(-1);
}

function isTopModal(id: symbol) {
  return topModal()?.id === id;
}

function restoreManagedInert() {
  managedInertElements.forEach((element) => {
    if (element.isConnected) element.removeAttribute("inert");
  });
  managedInertElements.clear();
}

function syncBackgroundIsolation() {
  restoreManagedInert();
  const top = topModal();
  if (!top) return;

  Array.from(document.body.children).forEach((element) => {
    if (
      !(element instanceof HTMLElement) ||
      element === top.backdrop ||
      element.hasAttribute("inert")
    )
      return;
    element.setAttribute("inert", "");
    managedInertElements.add(element);
  });
}

function registerModal(entry: ModalEntry) {
  if (modalStack.length === 0) {
    bodyOverflowBeforeModal = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    bodyObserver = new MutationObserver(syncBackgroundIsolation);
    bodyObserver.observe(document.body, { childList: true });
  }
  modalStack.push(entry);
  syncBackgroundIsolation();
}

function unregisterModal(id: symbol) {
  const index = modalStack.findIndex((entry) => entry.id === id);
  if (index === -1) return { wasTop: false, returnFocusCandidates: [] as HTMLElement[] };

  const wasTop = index === modalStack.length - 1;
  const [removed] = modalStack.splice(index, 1);
  if (!wasTop) {
    modalStack.slice(index).forEach((entry) => {
      removed!.returnFocusCandidates.forEach((candidate) => {
        if (!entry.returnFocusCandidates.includes(candidate))
          entry.returnFocusCandidates.push(candidate);
      });
    });
  }
  syncBackgroundIsolation();

  if (modalStack.length === 0) {
    bodyObserver?.disconnect();
    bodyObserver = null;
    document.body.style.overflow = bodyOverflowBeforeModal ?? "";
    bodyOverflowBeforeModal = null;
  }
  return { wasTop, returnFocusCandidates: removed!.returnFocusCandidates };
}

function isVisible(element: HTMLElement, container: HTMLElement) {
  let current: HTMLElement | null = element;
  while (current && container.contains(current)) {
    const style = window.getComputedStyle(current);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (current === container) break;
    current = current.parentElement;
  }
  return true;
}

function isTabbableRadio(element: HTMLInputElement) {
  if (element.type !== "radio" || !element.name) return true;
  const root: ParentNode = element.form ?? document;
  const group = Array.from(root.querySelectorAll<HTMLInputElement>("input[type='radio']")).filter(
    (radio) =>
      radio.name === element.name && radio.form === element.form && !radio.matches(":disabled"),
  );
  return (group.find((radio) => radio.checked) ?? group[0]) === element;
}

function focusableElements(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (element) => {
      if (element.closest("[inert],[hidden],[aria-hidden='true']")) return false;
      if (element.matches(":disabled") || element.tabIndex < 0) return false;
      const closedDetails = element.closest("details:not([open])");
      if (closedDetails && closedDetails.querySelector(":scope > summary") !== element)
        return false;
      if (element instanceof HTMLInputElement && !isTabbableRadio(element)) return false;
      return isVisible(element, container);
    },
  );
}

export function useModalDialog({
  open,
  onClose,
  backdropRef,
  dialogRef,
}: {
  open: boolean;
  onClose: () => void;
  backdropRef: RefObject<HTMLDivElement | null>;
  dialogRef: RefObject<HTMLElement | null>;
}) {
  const onCloseRef = useRef(onClose);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (open) return;

    const rememberFocus = () => {
      const focused = document.activeElement;
      if (!(focused instanceof HTMLElement)) return;
      queueMicrotask(() => {
        if (focused.isConnected && !dialogRef.current?.contains(focused))
          returnFocusRef.current = focused;
      });
    };
    rememberFocus();
    document.addEventListener("focusin", rememberFocus);
    return () => document.removeEventListener("focusin", rememberFocus);
  }, [dialogRef, open]);

  useEffect(() => {
    if (!open) return;

    const backdrop = backdropRef.current;
    const dialog = dialogRef.current;
    if (!backdrop || !dialog) return;

    const id = Symbol("modal");
    const returnFocus = returnFocusRef.current;
    registerModal({
      id,
      backdrop,
      dialog,
      returnFocusCandidates: returnFocus ? [returnFocus] : [],
    });

    let active = true;
    let focusContainmentQueued = false;
    const containFocus = () => {
      if (focusContainmentQueued) return;
      focusContainmentQueued = true;
      queueMicrotask(() => {
        focusContainmentQueued = false;
        if (!active || !isTopModal(id) || !dialog.isConnected) return;
        const focused = document.activeElement;
        if (focused instanceof HTMLElement && focused !== dialog && dialog.contains(focused))
          return;
        const target =
          dialog.querySelector<HTMLElement>("[autofocus]") ??
          focusableElements(dialog)[0] ??
          dialog;
        target.focus();
      });
    };
    containFocus();

    const onFocusOut = (event: FocusEvent) => {
      const nextFocused = event.relatedTarget;
      if (nextFocused instanceof Node && dialog.contains(nextFocused)) return;
      containFocus();
    };
    dialog.addEventListener("focusout", onFocusOut);

    const dialogObserver = new MutationObserver(() => {
      const focused = document.activeElement;
      if (!(focused instanceof Node) || !dialog.contains(focused)) containFocus();
    });
    dialogObserver.observe(dialog, { childList: true, subtree: true });

    const onKeyDown = (event: KeyboardEvent) => {
      if (!isTopModal(id) || event.defaultPrevented || event.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const focusable = focusableElements(dialog);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0]!;
      const last = focusable.at(-1)!;
      const focused = document.activeElement;
      if (
        event.shiftKey &&
        (focused === first || focused === dialog || !dialog.contains(focused))
      ) {
        event.preventDefault();
        last.focus();
      } else if (
        !event.shiftKey &&
        (focused === last || focused === dialog || !dialog.contains(focused))
      ) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      active = false;
      dialogObserver.disconnect();
      dialog.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("keydown", onKeyDown);
      const { wasTop, returnFocusCandidates } = unregisterModal(id);
      if (!wasTop) return;

      const returnFocus = returnFocusCandidates.find(
        (candidate) => candidate.isConnected && !candidate.closest("[inert]"),
      );
      if (returnFocus) returnFocus.focus();
      else topModal()?.dialog.focus();
    };
  }, [backdropRef, dialogRef, open]);
}
