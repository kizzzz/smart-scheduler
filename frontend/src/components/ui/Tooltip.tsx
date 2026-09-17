import { useEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const MAX_W = 260;
const EDGE = 8;

/**
 * 轻量 Tooltip：hover / focus 触发，使用 portal + fixed 定位，避免被网格单元裁剪。
 * fixed 定位是相对视口的，所以显示期间一旦发生滚动 / 缩放就直接收起，避免气泡与锚点错位。
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
    const half = MAX_W / 2;
    const x = Math.min(
      Math.max(r.left + r.width / 2, half + EDGE),
      Math.max(half + EDGE, window.innerWidth - half - EDGE),
    );
    setPos({ x, y: below ? r.bottom + 8 : r.top - 8, below });
  };

  const hide = () => setPos(null);

  useEffect(() => {
    if (!pos) return;
    // capture 阶段监听，任何祖先滚动容器（如看板横向滚动）都能收到
    window.addEventListener('scroll', hide, true);
    window.addEventListener('resize', hide);
    return () => {
      window.removeEventListener('scroll', hide, true);
      window.removeEventListener('resize', hide);
    };
  }, [pos]);

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
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
