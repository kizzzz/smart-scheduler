import { useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * 轻量 Tooltip：hover / focus 触发，使用 portal + fixed 定位，避免被网格单元裁剪。
 */
export function Tooltip({
  content,
  children,
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number; below: boolean } | null>(null);

  const show = () => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const below = r.top < 120;
    setPos({ x: r.left + r.width / 2, y: below ? r.bottom + 8 : r.top - 8, below });
  };

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      onFocus={show}
      onBlur={() => setPos(null)}
    >
      {children}
      {pos && content
        ? createPortal(
            <div
              role="tooltip"
              className="pointer-events-none fixed z-[60] max-w-[260px] animate-fade-in rounded-lg border border-[#26383605] bg-[#12211F] px-2.5 py-2 text-[11.5px] leading-relaxed text-white shadow-pop"
              style={{
                left: pos.x,
                top: pos.y,
                transform: `translate(-50%, ${pos.below ? '0' : '-100%'})`,
              }}
            >
              {content}
            </div>,
            document.body,
          )
        : null}
    </span>
  );
}
