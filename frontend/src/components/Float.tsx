import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

/** A floating panel anchored under (or above) an element. Closes on outside click or Escape. */
export function Float({ anchor, onClose, className = "", children, onEnter, onLeave }: {
  anchor: HTMLElement | null; onClose?: () => void; className?: string; children: React.ReactNode; onEnter?: () => void; onLeave?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null);

  useLayoutEffect(() => {
    if (!anchor || !ref.current) return;
    const r = anchor.getBoundingClientRect();
    const w = ref.current.offsetWidth, h = ref.current.offsetHeight;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    const below = r.bottom + 6;
    const top = below + h > window.innerHeight - 8 && r.top - h - 6 > 8 ? r.top - h - 6 : below;
    setPos({ left, top });
  }, [anchor, children]);

  useEffect(() => {
    if (!onClose) return;
    const down = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor?.contains(t)) onClose();
    };
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("pointerdown", down);
    document.addEventListener("keydown", key);
    return () => { document.removeEventListener("pointerdown", down); document.removeEventListener("keydown", key); };
  }, [anchor, onClose]);

  return createPortal(
    <div ref={ref} className={`float ${className}`} style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999 }} onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {children}
    </div>,
    document.body,
  );
}
