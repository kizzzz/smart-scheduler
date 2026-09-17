import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '../../lib/utils';

export function Dialog({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
  width = 'max-w-md',
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open || !mounted) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div
        className="absolute inset-0 bg-[#0B1614]/35 backdrop-blur-[1px]"
        onClick={onClose}
        aria-hidden
      />
      <div
        role="dialog"
        aria-modal="true"
        className={cn(
          'relative z-10 w-full animate-fade-in overflow-hidden rounded-card border border-line bg-white shadow-pop',
          width,
        )}
      >
        <div className="flex items-start gap-3 border-b border-line-2 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-[14px] font-semibold text-ink">{title}</h3>
            {subtitle ? <p className="mt-0.5 text-[11.5px] leading-relaxed text-mut">{subtitle}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭"
            className="flex-none rounded-md p-1 text-mut-2 transition-colors duration-150 hover:bg-line-2 hover:text-ink-2 focus-ring"
          >
            <X size={14} />
          </button>
        </div>
        <div className="max-h-[62vh] overflow-y-auto px-4 py-3 scrollbar-thin">{children}</div>
        {footer ? (
          <div className="flex items-center justify-end gap-2 border-t border-line-2 bg-soft px-4 py-2.5">
            {footer}
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
