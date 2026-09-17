import { cn } from '../../lib/utils';

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-shimmer rounded-md bg-[#EDF1F0]', className)} />;
}

export function SkeletonChip({ className }: { className?: string }) {
  return <Skeleton className={cn('h-[18px] w-[38px] rounded-[5px]', className)} />;
}
